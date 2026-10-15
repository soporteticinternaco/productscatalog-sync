import sys
import pandas as pd
import re
from elasticsearch import Elasticsearch, helpers
import mysql.connector
import logging
import json
import elastic_common
import os
import uuid

SUPPLIERS_DB_HOST = os.environ.get("SUPPLIERS_DB_HOST")
SUPPLIERS_DB_USER = os.environ.get("SUPPLIERS_DB_USER")
SUPPLIERS_DB_PWD = os.environ.get("SUPPLIERS_DB_PWD")
SUPPLIERS_DB_DATABASE = os.environ.get("SUPPLIERS_DB_DATABASE")

ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL")

mysql_config = {
    "host": SUPPLIERS_DB_HOST,
    "user": SUPPLIERS_DB_USER,
    "password": SUPPLIERS_DB_PWD,
    "database": SUPPLIERS_DB_DATABASE,
    "ssl_disabled": True
}

es = Elasticsearch([ELASTIC_SEARCH_URL])

# When DEBUG=1 a detailed trace is written to debug.log: when processing of an
# excel file starts/finishes, the operation resolved for each product ref
# (insert/update/delete) and any per-product error together with its ref.
DEBUG = os.environ.get("DEBUG") == "1"
debug_logger = logging.getLogger("debug")
debug_logger.propagate = False
if DEBUG:
    debug_logger.setLevel(logging.DEBUG)
    if not debug_logger.handlers:
        _debug_handler = logging.FileHandler("debug.log", mode="w")
        _debug_handler.setLevel(logging.DEBUG)
        _debug_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        debug_logger.addHandler(_debug_handler)


def log_debug(msg):
    if DEBUG:
        debug_logger.debug(msg)


def extract_supplier_id(filename):
    match = re.match(r"(\d+)_.*", filename)
    if not match:
        raise ValueError("Filename must start with supplier ID")
    return str(match.group(1))

def load_excel_data(filepath):
    df = pd.read_excel(filepath, header=0, dtype={
        "SKU": str,
        "EAN": str,
        "MainAssetThumbnail": str,
        "MainAssetURL": str,
        # Read category code columns as strings so that values formatted as
        # numbers in the Excel file (e.g. 5) are not turned into "5.0", and so
        # that empty cells become None instead of a float NaN (a NaN would
        # survive the df.where(...) below because numeric columns cannot hold
        # None, and str(NaN) yields the literal "nan").
        "CategoryID": str,
        "FamilyCode": str,
        "SubfamilyCode": str,
        "TypeCode": str
    })
    df = df.where(pd.notnull(df), None)
    return df


def fetch_supplier_metadata_from_db(supplier_id):
    try:
        conn = mysql.connector.connect(**mysql_config)

        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT description AS supplier_name, tags, visibility, margin_percent
            FROM supplier
            WHERE id = %s
        """, (supplier_id,))
        row = cursor.fetchone()

        if not row:
            raise ValueError(f"Supplier with ID {supplier_id} not found in DB.")

        # Ensure structure matches what Elasticsearch expects
        return {
            "supplier_name": row["supplier_name"],
            "tags": {"es": row["tags"] or "", "pt": row["tags"] or ""},
            "visibility": row["visibility"] or 0,
            "margin_percent": row["margin_percent"]
        }

    finally:
        if cursor: cursor.close()
        if conn: conn.close()
        
def generateId(tenant_id, supplier_id, id):
    return str(tenant_id.lower()) + "-" + str(supplier_id) + "-" + str(id)

        
def generate_doc(row, tenant_id, supplier_id, category_info, supplier_meta):
    ean = str(row['EAN']).strip()
    if ean == '0':
        ean = None
        
    net_price = float(row["PVP"])
    supplier_margin = float(supplier_meta["margin_percent"]) if supplier_meta["margin_percent"] is not None else 0
    
    # Update doc without including "id" field to respect what has come from mysql database
    update_doc = {
            "ref": row["SKU"],
            "ean": ean,
            "type": "edp",
            "description": {"es": row["Name"], "pt": row["Name"]},
            "short_description": {"es": row["Description"], "pt": row["Description"]} if row["Description"] and not pd.isna(row["Description"]) else None,
            "main_picture_url": row["MainAssetURL"] if row["MainAssetURL"] else None,
            "main_picture_thumb_url": row["MainAssetThumbnail"] if row["MainAssetThumbnail"] else None,
            "net_price": net_price,
            "vat_amount": float(row["TAX"]),
            "discount1": float(row["Discount1"]),
            "discount2": float(row["Discount2"]),
            "sale_price": float(row["PVP_FINAL"]),
            "container_units": int(row["QuantityPerPackage"]) if not pd.isna(row["QuantityPerPackage"]) else None,
            "container_type": {"es": row["PackagingType"], "pt": row["PackagingType"]},
            "step": int(row["StepQuantity"]) if not pd.isna(row["StepQuantity"]) else None,
            "deleted": False,
            "tenant_id": tenant_id,
            "supplier_id": supplier_id,
            "supplier_name": supplier_meta["supplier_name"],
            "tags": supplier_meta["tags"],
            "supplier_visibility": supplier_meta["visibility"],
            "net_price_with_margin": ((1 + supplier_margin) * net_price)
        }
    
    id = generateId(tenant_id, supplier_id, str(row['SKU']))
    
    # When updating "id" is NOT included
    # When upserting "id" is included
    doc = {
        "_op_type": "update",
        "_index": f"productscatalog-{tenant_id}-products-current",
        "_id": id,
        "doc": update_doc,
        "upsert": {
            "id": id,
            **update_doc        
        }
    }
    
    if category_info is not None:
        doc["doc"].update(category_info)
        doc["upsert"].update(category_info)
    
    return doc

def fetch_existing_product_ids(es, tenant_id, supplier_id):
    query = {
        "query": {
            "bool": {
                "must": [
                    {"term": { "tenant_id": tenant_id }},
                    {"term": { "supplier_id": supplier_id }}
                ]
            }
        },
        "_source": ["id"]
    }
    result = helpers.scan(es, index=f"productscatalog-{tenant_id}-products-current", query=query)
    return set([doc["_id"] for doc in result])


def main():
    # Use 'bulk' param to 0 to disable bulk imports and process one by one so if something fails we know which product failed
    if len(sys.argv) not in [3, 4]:
        logging.error("Usage: python import_products.py <tenant_id> <excel_file> [bulk]")
        sys.exit(1)

    tenant_id = str(sys.argv[1]).lower()
    elastic_common.config_logging(f"error_log_{tenant_id}.log")

    filepath = sys.argv[2]
    
    is_bulk = True
    if len(sys.argv) == 4:
        is_bulk = sys.argv[3].lower() not in ['false', '0', 'f']

    file_name = os.path.basename(filepath)
    
    logging.info(f"[PROD][{filepath}]: importing products from excel file")
    log_debug(f"START processing excel file: {file_name}")

    supplier_id = extract_supplier_id(file_name)
    df = load_excel_data(filepath)

    supplier_meta = fetch_supplier_metadata_from_db(supplier_id)
    
    existing_ids = fetch_existing_product_ids(es, tenant_id, supplier_id)
    imported_ids = set()

    # Load all categories once
    elsabio_categories_map = elastic_common.generate_categories_map(elastic_common.fetch_all_categories(es, f"productscatalog-{tenant_id}-categories", tenant_id, f'{tenant_id.lower()}own'))
   
    categories_map = elastic_common.generate_categories_map(elastic_common.fetch_all_categories(es,f"productscatalog-{tenant_id}-categories", tenant_id, supplier_id))

    actions = []
    for idx, row in df.iterrows():
        sku = row["SKU"]
        if pd.isna(sku):
            continue

        try:
            category_id = str(row["CategoryID"]) if row["CategoryID"] is not None else None

            if category_id is None:
                l1 = str(row["FamilyCode"]) if row["FamilyCode"] is not None and row["FamilyCode"] != "-" else ""
                l2 = str(row["SubfamilyCode"]) if row["SubfamilyCode"] is not None and row["SubfamilyCode"] != "-" else ""
                l3 = str(row["TypeCode"]) if row["TypeCode"] is not None and row["TypeCode"] != "-" else ""

                category_id = l3 or l2 or l1

            category_info = elastic_common.find_category_hierarchy(category_id, categories_map)

            if category_info is None:
                category_info = elastic_common.find_category_hierarchy(category_id, elsabio_categories_map)

            doc = generate_doc(row, tenant_id, supplier_id, category_info, supplier_meta)

            # An upsert resolves to an update when the id already exists, otherwise an insert
            operation = "update" if doc["_id"] in existing_ids else "insert"
            log_debug(f"ref={sku} operation={operation}")

            if is_bulk:
                actions.append(doc)
                imported_ids.add(doc["_id"])
            else:
                if not bulk:logging.info("Adding: " + json.dumps(doc["doc"], indent=2))
                es.update(
                    index=doc["_index"],
                    id=doc["_id"],
                    body={
                        "doc": doc["doc"],
                        "upsert": doc["upsert"]
                    }
                )
                imported_ids.add(doc["_id"])
        except Exception as e:
            log_debug(f"ref={sku} ERROR {e}")
            logging.error(f"Update failed for product SKU: {sku}. Error: {e}")
            elastic_common.write_import_summary(f"Update failed for product SKU {sku}: {e}")
            logging.info("Stopping script after first failure.")
            sys.exit(1)

        # Bulk upload in chunks of 500 for performance
        if is_bulk and len(actions) >= 500:
            logging.info("Bulk upload chunk is 500: sending chunk actions to elastic")
            helpers.bulk(es, actions)
            actions = []

    if actions and is_bulk:
        logging.info("Sending remaining actions to elastic")
        helpers.bulk(es, actions)

    # Disable removed products
    to_disable = existing_ids - imported_ids
    if to_disable:
        logging.info("Disabling " + str(len(to_disable)) + " products")
        
        for pid in to_disable:
            log_debug(f"ref={pid} operation=delete")

        if is_bulk:
            disable_actions = [
                {
                    "_op_type": "update",
                    "_index": f"productscatalog-{tenant_id}-products-current",
                    "_id": pid,
                    "doc": {"deleted": True}
                } for pid in to_disable
            ]
            helpers.bulk(es, disable_actions)
        else:
            for pid in to_disable:
                try:
                    es.update(
                        index=f"productscatalog-{tenant_id}-products",
                        id=pid,
                        body={"doc": {"deleted": True}}
                    )
                except Exception as e:
                    log_debug(f"ref={pid} ERROR {e}")
                    logging.error(f"1-by-1 Disable failed for product ID: {pid}. Error: {e}")
                    elastic_common.write_import_summary(f"Disable failed for product ID {pid}: {e}")
                    logging.info("Stopping script after first failure.")
                    sys.exit(1)

    logging.info(f"Imported/Updated: {len(imported_ids)}, Disabled: {len(to_disable)}")
    elastic_common.write_import_summary(f"imported/updated: {len(imported_ids)}, disabled: {len(to_disable)}")
    log_debug(f"FINISH processing excel file: {file_name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Fatal error during dump: {str(e)}", exc_info=True)
        elastic_common.write_import_summary(f"Fatal error: {str(e)}")
        exit(1)
