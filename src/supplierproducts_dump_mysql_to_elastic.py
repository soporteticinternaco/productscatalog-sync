import mysql.connector
from elasticsearch import Elasticsearch, helpers
import argparse
import elastic_common
import logging
import json
import os

SUPPLIERS_DB_HOST = os.environ.get("SUPPLIERS_DB_HOST")
SUPPLIERS_DB_USER = os.environ.get("SUPPLIERS_DB_USER")
SUPPLIERS_DB_PWD = os.environ.get("SUPPLIERS_DB_PWD")
SUPPLIERS_DB_DATABASE = os.environ.get("SUPPLIERS_DB_DATABASE")

ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL")

parser = argparse.ArgumentParser(description="Dump supplier products from mysql to Elasticsearch")
parser.add_argument("tenant_id", help="Needs tenantId")
args = parser.parse_args()

tenant_id = str(args.tenant_id).lower()

# MySQL connection config
mysql_config = {
    "host": SUPPLIERS_DB_HOST,
    "user": SUPPLIERS_DB_USER,
    "password": SUPPLIERS_DB_PWD,
    "database": SUPPLIERS_DB_DATABASE,
    "ssl_disabled": True
}

# Elasticsearch connection
products_index_name = f"productscatalog-{tenant_id}-products-current"
categories_index_name = f"productscatalog-{tenant_id}-categories"

es = Elasticsearch([ELASTIC_SEARCH_URL])

elastic_common.config_logging(f"error_log_{tenant_id}.log")

def fetch_products():
    connection = mysql.connector.connect(**mysql_config)
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT sp.*, s.description AS supplier_name, s.portal, s.visibility, s.tags AS tags, s.margin_percent
        FROM supplier_product sp
        JOIN supplier s ON s.id = sp.supplier_id 
        order by sp.supplier_id asc
    """)

    for row in cursor:
        yield row

    cursor.close()
    connection.close()
    
def generateId(tenant_id, supplier_id, id):
    return str(tenant_id).lower() + "-" + str(supplier_id) + "-" + str(id)


def transform_row(row, categories_map, elsabio_categories_map):
    
    l1 = row["family_id"]
    l2 = row["subfamily_id"]
    l3 = row["type_id"]
    
    category_id =  l3 if l3 is not None else l2 if l2 is not None else l1
    
    category = elastic_common.find_category_hierarchy(
        category_id, categories_map, elsabio_categories_map, path=(l1, l2, l3)
    )
    
    #if category is None:
    #    category = elastic_common.find_category_hierarchy(category_id, elsabio_categories_map)
    
    net_price = float(row["net_price"])
    supplier_margin = float(row["margin_percent"]) if row["margin_percent"] is not None else 0
    
    return {
        "_index": products_index_name,
        "_id": generateId(tenant_id, row["supplier_id"], row["ref"]),
        "_source": {
            "id": row["id"],
            "ref": row["ref"],
            "ean": row["ean"],
            "sale_price": float(row["sale_price"]) if row["sale_price"] is not None else None,
            "net_price": net_price,
            "vat_amount": float(row["vat_amount"]) if row["vat_amount"] is not None else None,
            "discount1": float(row["discount1"]) if row["discount1"] is not None else None,
            "discount2": float(row["discount2"]) if row["discount2"] is not None else None,
            "container_units": row["container_units"],
            "container_type": {
                "es": row["container_type"]
            },
            "tenant_id": tenant_id,
            "supplier_id": str(row['supplier_id']),
            "supplier_name": row["supplier_name"],
            "portal": row["portal"],
            "supplier_visibility": row["visibility"],
            "deleted": bool(row["deleted"]),
            "step": row["step"],
            "tags": {
                "es": row["tags"] if row["tags"] else None
            },
            "description": {
                "es": row["description"] if row["description"] else None
            },
            "short_description": {
                "es": row["short_description"] if row["short_description"] else None
            },
            "main_picture_url": row["main_picture_url"],
            "main_picture_thumb_url": row["main_picture_thumb_url"],
            "net_price_with_margin": ((1 + supplier_margin) * net_price),
            "grouping_code": None,
            "grouping_value": None,
            "novelty": None,
            "offer": None,
            "remarked": None,
            "segment": None,
            "promotion_code": None,
            "type": "edp",
            **category
        }
    } if category is not None else None


def dump_to_elasticsearch():
    rows = fetch_products()
    current_supplier_id = None

    elsabio_categories_map = elastic_common.generate_categories_map(elastic_common.fetch_all_categories(es, categories_index_name, tenant_id, f'{tenant_id}-own'))

    categories_map = {}
    actions = []

    for row in rows:
        supplier_id = row['supplier_id']
    
        if current_supplier_id != supplier_id:
            current_supplier_id = supplier_id
            logging.error("--- Importing supplier: " + str(current_supplier_id))
            categories_map = elastic_common.generate_categories_map(elastic_common.fetch_all_categories(es, categories_index_name, tenant_id, current_supplier_id))
            
            if categories_map is None or len(categories_map.keys()) == 0:
                categories_map = elsabio_categories_map

        action = transform_row(row, categories_map, elsabio_categories_map)
        if action is not None:
            actions.append(action)
        else:
            category_id = row["type_id"] if row["type_id"] is not None \
                else row["subfamily_id"] if row["subfamily_id"] is not None \
                else row["family_id"] if row["family_id"] is not None \
                else ""
                
            logging.error("Product '" + row["ref"] + "' cannot be added because could not find category '" + category_id + "'")

    helpers.bulk(es, actions)
    logging.info("✅ Data successfully indexed to Elasticsearch.")

if __name__ == "__main__":
    try:
        dump_to_elasticsearch()
    except Exception as e:
        logging.error(f"Fatal error during dump: {str(e)}", exc_info=True)