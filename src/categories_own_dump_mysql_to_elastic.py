import mysql.connector
from elasticsearch import Elasticsearch, helpers
import argparse
import os
import re
import elastic_common
import logging
import json

EXTRANET_DB_HOST = os.environ.get("EXTRANET_DB_HOST")
EXTRANET_DB_USER = os.environ.get("EXTRANET_DB_USER")
EXTRANET_DB_PWD = os.environ.get("EXTRANET_DB_PWD")
EXTRANET_DB_DATABASE = os.environ.get("EXTRANET_DB_DATABASE")

ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL")

mysql_config = {
    "host": EXTRANET_DB_HOST,
    "user": EXTRANET_DB_USER,
    "password": EXTRANET_DB_PWD,
    "database": EXTRANET_DB_DATABASE,
    "ssl_disabled": True
}


parser = argparse.ArgumentParser(description="Dump own categories to Elasticsearch")
parser.add_argument("tenant_id", help="Needs tenantId")
parser.add_argument("supplier_id", help="Needs supplierId")
args = parser.parse_args()

tenant_id = args.tenant_id.lower()
supplier_id = args.supplier_id

elastic_common.config_logging(f"error_log_{tenant_id}.log")

logging.info("Importing cats for supplier_id: " + str(supplier_id))

elastic = Elasticsearch([ELASTIC_SEARCH_URL])

ES_INDEX = f"productscatalog-{tenant_id}-categories"

def connect_mysql():
    return mysql.connector.connect(**mysql_config)

def fetch_categories():
    query = """
    SELECT DISTINCT
        awbl.id_web_brand AS l1, awbl.description AS l1Description,
        aptl.id_product_type AS l2, aptl.description AS l2Description,
        awcl.id_web_category AS l3, awcl.category_name AS l3Description,
        al.iso_639_1 AS lang
    FROM app_web_brand_lang awbl join app_web_brand wb ON awbl.id_web_brand = wb.id_web_brand
    JOIN app_product_type_lang aptl join app_product_type pt ON aptl.id_product_type = pt.id_product_type
    JOIN app_web_category_lang awcl join app_web_category wc ON awcl.id_web_category = wc.id_web_category
    JOIN app_language al
      ON awbl.id_language = al.id_language
     AND aptl.id_language = al.id_language
     AND awcl.id_language = al.id_language
    JOIN app_product p
      ON p.id_web_brand = awbl.id_web_brand
     AND p.id_product_type = aptl.id_product_type
     AND p.id_web_category = awcl.id_web_category
    JOIN app_product_brand pb
      ON pb.id_product_brand = p.id_product_brand
    ORDER BY lang, l1, l2, l3
    """

    conn = connect_mysql()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def normalize_lang(lang):
    return lang.lower().strip()

ALL_NINES_PATTERN = re.compile(r"^9+$")

def is_sentinel_id(category_id):
    return bool(ALL_NINES_PATTERN.fullmatch(category_id))

def build_category_docs(rows):
    docs = {}

    for row in rows:
        l1 = str(row["l1"])
        l2 = str(row["l2"])
        l3 = str(row["l3"])
        lang = normalize_lang(row["lang"])

        # level1 is mandatory; level2/level3 only apply, and only need
        # checking, once that level is actually reached.
        l1_disabled = is_sentinel_id(l1)
        l2_disabled = l1_disabled or is_sentinel_id(l2)
        l3_disabled = l2_disabled or is_sentinel_id(l3)

        # Level 1
        doc_id_1 = generateId(tenant_id, supplier_id, l1)
        if doc_id_1 not in docs:
            docs[doc_id_1] = {
                "tenant_id": tenant_id,
                "supplier_id": supplier_id,
                "level1": l1,
                "disabled": l1_disabled,
                "description": {}
            }
        docs[doc_id_1]["description"][lang] = row["l1Description"]

        # Level 2
        doc_id_2 = generateId(tenant_id, supplier_id, l2)
        if doc_id_2 not in docs:
            docs[doc_id_2] = {
                "tenant_id": tenant_id,
                "supplier_id": supplier_id,
                "level1": l1,
                "level2": l2,
                "disabled": l2_disabled,
                "description": {}
            }
        docs[doc_id_2]["description"][lang] = row["l2Description"]

        # Level 3
        doc_id_3 = generateId(tenant_id, supplier_id, l3)
        if doc_id_3 not in docs:
            docs[doc_id_3] = {
                "tenant_id": tenant_id,
                "supplier_id": supplier_id,
                "level1": l1,
                "level2": l2,
                "level3": l3,
                "disabled": l3_disabled,
                "description": {}
            }
        docs[doc_id_3]["description"][lang] = row["l3Description"]

    return docs

def fetch_existing_ids():
    query = {
        "_source": False,
        "query": {
            "bool": {
                "must": [
                    {"term": { "tenant_id.keyword": tenant_id }},
                    {"term": { "supplier_id.keyword": supplier_id }}
                ]
            }
        }
    }
    return {doc["_id"] for doc in helpers.scan(elastic, index=ES_INDEX, query=query)}

def generateId(tenant_id, supplier_id, id):
    return str(tenant_id + "-" + supplier_id + "-" + id)

def upsert_and_disable(docs, existing_ids):
    new_ids = set(docs.keys())
    to_disable = existing_ids - new_ids

    actions = []

    # Upsert new or updated docs
    for doc_id, doc in docs.items():
        actions.append({
            "_op_type": "index",
            "_index": ES_INDEX,
            "_id": doc_id,
            "_source": doc,
            "doc_as_upsert": True
        })

    # Disable deleted ones
    for doc_id in to_disable:
        actions.append({
            "_op_type": "update",
            "_index": ES_INDEX,
            "_id": doc_id,
            "doc": { "disabled": True }
        })
        
    print("Upserting " + str(len(new_ids)) + " documents")
    print("Disabling " + str(len(to_disable)) + " documents")

    helpers.bulk(elastic, actions)

def main():
    print("Fetching data from MySQL...")
    rows = fetch_categories()
    print(f"Fetched {len(rows)} rows from MySQL.")

    print("Building category documents...")
    docs = build_category_docs(rows)
    print(f"Built {len(docs)} unique category documents.")

    print("Fetching existing Elasticsearch IDs...")
    existing_ids = fetch_existing_ids()
    print(f"Fetched {len(existing_ids)} existing IDs from Elasticsearch.")

    print(f"New/updated categories: {len(docs) - len(existing_ids)}")
    print(f"Disabled categories: {len(existing_ids) - len(docs)}")
    print("Indexing new/updated categories and disabling removed ones...")
    upsert_and_disable(docs, existing_ids)

    print("Done.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Fatal error during dump: {str(e)}", exc_info=True)