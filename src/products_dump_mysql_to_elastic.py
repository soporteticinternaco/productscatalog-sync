import mysql.connector
from elasticsearch import Elasticsearch, helpers
import argparse
import elastic_common
import logger
import logging
import json
import os

EXTRANET_DB_HOST = os.environ.get("EXTRANET_DB_HOST")
EXTRANET_DB_USER = os.environ.get("EXTRANET_DB_USER")
EXTRANET_DB_PWD = os.environ.get("EXTRANET_DB_PWD")
EXTRANET_DB_DATABASE = os.environ.get("EXTRANET_DB_DATABASE")

ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL")

tenants_map = {
    "elsabio": {
        "name": "El Sabio",
    },
    "internaco": {
        "name": "Internaco"
    },
    "sa92": {
        "name": "SA92"
    },
    "maxrain": {
        "name": "Maxrain"
    },
    "ipmax": {
        "name": "IpMax"
    }
}

parser = argparse.ArgumentParser(description="Dump products from mysql to Elasticsearch")
parser.add_argument("tenant_id", help="Needs tenantId")
args = parser.parse_args()

tenant_id = str(args.tenant_id).lower()

# MySQL connection config
mysql_config = {
    "host": EXTRANET_DB_HOST,
    "user": EXTRANET_DB_USER,
    "password": EXTRANET_DB_PWD,
    "database": EXTRANET_DB_DATABASE,
    "ssl_disabled": True
}

# Elasticsearch connection
index_name = f"productscatalog-{tenant_id}-products"
categories_index_name = f"productscatalog-{tenant_id}-categories"
es = Elasticsearch([ELASTIC_SEARCH_URL])

elastic_common.config_logging(f"error_log_{tenant_id}.log")

def fetch_products():
    connection = mysql.connector.connect(**mysql_config)
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
    SELECT p.id_product as id, p.as400_id as ref, p.codigo_ean as ean, p.id_web_brand as l1Id, p.id_product_type as l2Id, p.id_web_category as l3Id, 
    p.id_product_brand as supplier_id, pb.name as supplier_name, 	
	not (enabled and show_in_web) as deleted, p.unidades_caja, p.tag, 
	pl1.title as title_es,
	case
		when pl2.title is null then pl1.title
		else pl2.title
	end as title_pt,
	p.codigo_padre_combinacion as grouping_code,
	p.atributo_combinacion as grouping_value,
	p.novedad as novelty,
	p.oferta as offer,
	p.destacado as highlight,
	p.tipo_producto_maq_fer as segment,
	p.codigo_promocion as promotion_code
        FROM app_product p 
        inner join app_product_lang pl1 on p.id_product = pl1.id_product 
        inner join app_language l1 on l1.id_language = pl1.id_language and l1.iso_639_1 ='ES'
        left join app_product_lang pl2 on p.id_product = pl2.id_product 
        inner join app_language l2 on l2.id_language = pl2.id_language and l2.iso_639_1 ='PT' 
        inner join app_product_brand pb on pb.id_product_brand = p.id_product_brand
    """)

    for row in cursor:
        yield row

    cursor.close()
    connection.close()
    
def generateId(tenant_id, supplier_id, id):
    return str(tenant_id).lower() + "-" + str(supplier_id) + "-" + str(id)


def fetch_existing_product_ids(es, index_name, tenant_id):
    query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"tenant_id.keyword": tenant_id}},
                    {"term": {"type.keyword": "own"}}
                ]
            }
        },
        "_source": False
    }
    result = helpers.scan(es, index=index_name, query=query)
    return set([doc["_id"] for doc in result])


def transform_row(row, categories_map):
    
    l1 = row["l1Id"]
    l2 = row["l2Id"]
    l3 = row["l3Id"]
    
    category_id =  l3 if l3 is not None else l2 if l2 is not None else l1
    
    category = elastic_common.find_category_hierarchy(category_id, categories_map)
    
    if category is None:
        logging.error("Product '" + row["ref"] + "' cannot be added because could not find category '" + category_id + "'")
        return None

    supplier_id = str(row['supplier_id']) if row['supplier_id'] is not None else None

    return {
        "_index": index_name,
        "_id": generateId(tenant_id, supplier_id, row["ref"]),
        "_source": {
            "id": row["id"],
            "ref": row["ref"],
            "ean": row["ean"],
            "sale_price": None,
            "net_price": None,
            "vat_amount": None,
            "discount1": None,
            "discount2": None,
            "container_units": None,
            "container_type": None,
            "tenant_id": tenant_id,
            "supplier_id": supplier_id,
            "supplier_name": row["supplier_name"],
            "portal": None,
            "supplier_visibility": None,
            "deleted": bool(row['deleted']),
            "step": row["unidades_caja"],
            "tags": {
                "es": row["tag"] if row["tag"] else None
            },
            "description": None,
            "short_description": {
                "es": row["title_es"] if row["title_es"] else None,
                "pt": row["title_pt"] if row["title_pt"] else None,
            },
            "main_picture_url": None,
            "main_picture_thumb_url": None,
            "net_price_with_margin": None,
            "grouping_code": row["grouping_code"],
            "grouping_value": row["grouping_value"],
            "novelty": row["novelty"],
            "offer": row["offer"],
            "highlight": row["highlight"],
            "segment": row["segment"],
            "promotion_code": row["promotion_code"],
            "type": "own",
            **category
        }
    } if category is not None else None


def dump_to_elasticsearch():
    rows = fetch_products()

    categories_map = elastic_common.generate_categories_map(elastic_common.fetch_all_categories(es, categories_index_name, tenant_id, f"{tenant_id}-own"))

    existing_ids = fetch_existing_product_ids(es, index_name, tenant_id)
    imported_ids = set()

    actions = []
    for row in rows:
        action = transform_row(row, categories_map)

        if action is not None:
            actions.append(action)
            imported_ids.add(action["_id"])
        else:
            category_id = row["l3Id"] if row["l3Id"] is not None \
                else row["l2Id"] if row["l2Id"] is not None \
                else row["l1Id"] if row["l1Id"] is not None \
                else ""
                
            logging.error("Product '" + row["ref"] + "' cannot be added because could not find category '" + category_id + "'")


    helpers.bulk(es, actions)

    # Disable removed products
    to_disable = existing_ids - imported_ids
    if to_disable:
        logging.info(f"Disabling {len(to_disable)} products no longer present in MySQL")
        disable_actions = [
            {
                "_op_type": "update",
                "_index": index_name,
                "_id": pid,
                "doc": {"deleted": True}
            } for pid in to_disable
        ]
        helpers.bulk(es, disable_actions)

    logging.info(f"✅ Data successfully indexed to Elasticsearch index: {index_name}. Imported: {len(imported_ids)}, Disabled: {len(to_disable)}")


if __name__ == "__main__":
    try:
        dump_to_elasticsearch()
    except Exception as e:
        logging.error(f"Fatal error during dump: {str(e)}", exc_info=True)