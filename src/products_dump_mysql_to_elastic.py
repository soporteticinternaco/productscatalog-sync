import mysql.connector
from elasticsearch import Elasticsearch, helpers
import argparse
import elastic_common
import logger
import logging
import json
import os
import re
from productsservice_client import ProductsServiceClient

EXTRANET_DB_HOST = os.environ.get("EXTRANET_DB_HOST")
EXTRANET_DB_USER = os.environ.get("EXTRANET_DB_USER")
EXTRANET_DB_PWD = os.environ.get("EXTRANET_DB_PWD")
EXTRANET_DB_DATABASE = os.environ.get("EXTRANET_DB_DATABASE")

ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL")

# Characteristic names (PIM keys) to exclude from indexing. Names differ per
# language, so add every language variant that should be dropped. Matching is
# case-insensitive and trim-insensitive.
EXCLUDED_CHARACTERISTICS = []
#    "Descripción de producto",   # es
#    "Descrição do produto",      # pt
#]

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
# Rebuild target. The -current alias will be pointed at -v2 once the rebuild is complete.
index_name = f"productscatalog-{tenant_id}-products-current"
categories_index_name = f"productscatalog-{tenant_id}-categories"
es = Elasticsearch([ELASTIC_SEARCH_URL])

elastic_common.config_logging(f"error_log_{tenant_id}.log")

# All-nines ids (e.g. 999999) are the extranet's "not assigned" sentinel.
# Keep in sync with categories_own_dump_mysql_to_elastic.py.
ALL_NINES_PATTERN = re.compile(r"^9+$")


def is_sentinel_id(value):
    return value is not None and bool(ALL_NINES_PATTERN.fullmatch(str(value)))


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


def fetch_product_prices():
    connection = mysql.connector.connect(**mysql_config)
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT id_product, id_rate, price FROM app_product_price
    """)

    prices_by_product = {}
    for row in cursor:
        prod_id = row['id_product']
        if prod_id not in prices_by_product:
            prices_by_product[prod_id] = []

        prices_by_product[prod_id].append({
            "id": row['id_rate'],
            "price": float(row['price'])
        })

    cursor.close()
    connection.close()
    return prices_by_product


def fetch_product_replacements():
    connection = mysql.connector.connect(**mysql_config)
    cursor = connection.cursor(dictionary=True)

    # `portal` is not filtered on: this DB is already scoped to a single
    # tenant (extranet_<tenant>), so every row here applies to this index.
    cursor.execute("""
        SELECT id_product, old_as400_id FROM app_product_replacements
    """)

    replacements_by_product = {}
    for row in cursor:
        prod_id = row['id_product']
        if prod_id not in replacements_by_product:
            replacements_by_product[prod_id] = []

        replacements_by_product[prod_id].append(row['old_as400_id'])

    cursor.close()
    connection.close()
    return replacements_by_product


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


def transform_row(row, categories_map, prices_by_product, pictures_map, replacements_by_product):

    l1 = str(row["l1Id"]) if row["l1Id"] is not None else None
    l2 = str(row["l2Id"]) if row["l2Id"] is not None else None
    l3 = str(row["l3Id"]) if row["l3Id"] is not None else None

    deleted = bool(row['deleted'])

    if is_sentinel_id(l1) or is_sentinel_id(l2) or is_sentinel_id(l3):
        # Any all-nines level (999999 = "not assigned"): index the product
        # with exactly the values MySQL has, and mark it as deleted.
        category = {
            "level1": l1,
            "level2": l2,
            "level3": l3,
            "level1Name": elastic_common.get_category_level_name(l1, categories_map),
            "level2Name": elastic_common.get_category_level_name(l2, categories_map),
            "level3Name": elastic_common.get_category_level_name(l3, categories_map),
        }
        deleted = True
    else:
        category_id = l3 if l3 is not None else l2 if l2 is not None else l1

        if category_id is None:
            logging.error(f"Product '{row['ref']}' cannot be added because it has no usable category "
                          f"(l1Id={row['l1Id']}, l2Id={row['l2Id']}, l3Id={row['l3Id']})")
            return None

        category = elastic_common.find_category_hierarchy(category_id, categories_map, path=(l1, l2, l3))

        if category is None:
            logging.error(f"Product '{row['ref']}' cannot be added because could not find category '{category_id}' "
                          f"(l1Id={row['l1Id']}, l2Id={row['l2Id']}, l3Id={row['l3Id']})")
            return None

    supplier_id = str(row['supplier_id']) if row['supplier_id'] is not None else None

    pictures = pictures_map.get(str(row["ref"]), {})

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
            "rates": prices_by_product.get(row["id"], []),
            "replaces": replacements_by_product.get(row["id"], []),
            "tenant_id": tenant_id,
            "supplier_id": supplier_id,
            "supplier_name": row["supplier_name"],
            "portal": None,
            "supplier_visibility": None,
            "deleted": deleted,
            "step": row["unidades_caja"],
            "tags": {
                "es": row["tag"] if row["tag"] else None
            },
            "description": {
                "es": row["title_es"] if row["title_es"] else None,
                "pt": row["title_pt"] if row["title_pt"] else None,
            },
            "short_description": None,
            "main_picture_url": pictures.get("main_picture_url"),
            "main_picture_thumb_url": pictures.get("main_picture_thumb_url"),
            "pictures": pictures.get("pictures", []),
            "characteristics": pictures.get("characteristics", []),
            "files": pictures.get("files", {"es": [], "pt": []}),
            "net_price_with_margin": None,
            "grouping_code": row["grouping_code"] if row["grouping_code"] else f"single-{str(row['ref'])}",
            "grouping_value": row["grouping_value"] if row["grouping_value"] else None,
            "novelty": row["novelty"],
            "offer": row["offer"],
            "highlight": row["highlight"],
            "segment": row["segment"],
            "promotion_code": row["promotion_code"],
            "type": "own",
            **category
        }
    }


def dump_to_elasticsearch():
    logging.info(f"Starting products dump for tenant '{tenant_id}'")

    logging.info("Fetching products from MySQL")
    rows = list(fetch_products())
    logging.info(f"Fetched {len(rows)} products from MySQL")

    logging.info("Fetching categories from Elasticsearch")
    categories_map = elastic_common.generate_categories_map(elastic_common.fetch_all_categories(es, categories_index_name, tenant_id, f"{tenant_id}-own"))

    logging.info("Fetching product prices from MySQL")
    prices_by_product = fetch_product_prices()

    logging.info("Fetching product replacements from MySQL")
    replacements_by_product = fetch_product_replacements()

    products_client = ProductsServiceClient(
        company_id=tenant_id,
        excluded_characteristics=EXCLUDED_CHARACTERISTICS,
    )
    pictures_map = products_client.fetch_main_pictures(row["ref"] for row in rows)

    logging.info("Fetching existing product ids from Elasticsearch")
    existing_ids = fetch_existing_product_ids(es, index_name, tenant_id)
    imported_ids = set()

    logging.info(f"Transforming {len(rows)} products for indexing")
    actions = []
    failed = 0
    for row in rows:
        # One bad row must not abort the whole dump (that leaves the index
        # stale for every product): log it and move on.
        try:
            action = transform_row(row, categories_map, prices_by_product, pictures_map, replacements_by_product)
        except Exception:
            failed += 1
            logging.error(f"Product '{row.get('ref')}' failed to transform", exc_info=True)
            continue

        if action is not None:
            actions.append(action)
            imported_ids.add(action["_id"])
        else:
            failed += 1

    if failed:
        logging.warning(f"{failed} of {len(rows)} products were skipped (see errors above)")

    if not actions:
        logging.error("No products could be transformed; aborting without touching the index")
        return

    logging.info(f"Indexing {len(actions)} products to Elasticsearch index '{index_name}'")
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
