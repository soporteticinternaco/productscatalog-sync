import os
import re
import json
import logging
import argparse
import pandas as pd
from elasticsearch import Elasticsearch, helpers
import os
import elastic_common

ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL")

elastic_common.config_logging()

# Excel file path
parser = argparse.ArgumentParser(description="Dump categories Excel file to Elasticsearch")
parser.add_argument("tenant_id", help="Needs tenantId")
parser.add_argument("file_path", help="Path to the Excel file (e.g., /mnt/data/852_categories_21.xlsx)")
args = parser.parse_args()

tenant_id = str(args.tenant_id)
file_path = args.file_path
file_name = os.path.basename(file_path)


def generateId(tenant_id, supplier_id, id):
    return str(tenant_id) + "-" + str(supplier_id) + "-" + str(id)

def resolve_hierarchy(cat_id):
    levels = {'level1': None, 'level2': None, 'level3': None}
    current = cat_id
    depth = 0
    while current and depth < 3:
        parent = category_map.get(current)
        if not parent:
            break
        if depth == 0:
            levels['level3'] = current
        elif depth == 1:
            levels['level2'] = current
        elif depth == 2:
            levels['level1'] = current
        current = parent['parent_id']
        depth += 1
    if levels['level1'] is None and levels['level2']:
        levels['level1'], levels['level2'], levels['level3'] = levels['level2'], levels['level3'], None
    elif levels['level1'] is None and levels['level3']:
        levels['level1'], levels['level2'], levels['level3'] = levels['level3'], None, None
    return levels

logging.info(f"[CAT][{file_path}]: importing categories from excel file")

# Extract supplier_id from file name
match = re.match(r"(\d+)_", file_name)
if not match:
    raise ValueError("Supplier ID not found at beginning of filename.")
supplier_id = str(match.group(1))

# Load Excel
df = pd.read_excel(file_path, header=0)
df.columns = ['category_id', 'parent_id', 'description_es']

index_name = "productscatalog-categories"
category_map = {}

# Elasticsearch connection
es = Elasticsearch([ELASTIC_SEARCH_URL])

# Load existing categories from ES
existing_docs = {}
query = {
    "query": {
        "bool": {
            "must": [
                {"term": { "tenant_id": tenant_id }},
                {"term": { "supplier_id": supplier_id }}
            ]
        }
    },
    "_source": ["level1", "level2", "level3", "description", "disabled"]
}

resp = helpers.scan(es, query=query, index=index_name)
for hit in resp:
    existing_docs[hit["_id"]] = hit

# Prepare bulk operations
bulk_ops = []
processed_ids = set()

# Build category map from Excel
category_map = {
    str(row['category_id']): {
        'description_es': row['description_es'],
        'parent_id': str(row['parent_id']) if pd.notna(row['parent_id']) else None
    }
    for _, row in df.iterrows()
}

for cat_id, info in category_map.items():
    levels = resolve_hierarchy(cat_id)
    doc = {
        "tenant_id": tenant_id,
        "supplier_id": supplier_id,
        "level1": levels['level1'],
        "level2": levels['level2'],
        "level3": levels['level3'],
        "description": {"es": info['description_es']},
        "disabled": False
    }

    logging.error("Add to processed: " + cat_id)
    
    _id = generateId(tenant_id, supplier_id, cat_id)
    
    processed_ids.add(_id)

    existing = existing_docs.get(_id)
    
    if existing:
        src = existing["_source"]
        needs_update = (
            src.get("description", {}).get("es") != info['description_es'] or
            src.get("level1") != levels['level1'] or
            src.get("level2") != levels['level2'] or
            src.get("level3") != levels['level3'] or
            src.get("disabled", False)
        )
        if needs_update:
            bulk_ops.append({
                "_op_type": "update",
                "_index": index_name,
                "_id": cat_id,
                "doc": doc
            })
    else:
        bulk_ops.append({
            "_op_type": "index",
            "_index": index_name,
            "_id": _id,
            "_source": doc
        })

# Disable missing categories
to_disable = set(existing_docs.keys()) - processed_ids
for cat_id in to_disable:
    bulk_ops.append({
        "_op_type": "update",
        "_index": index_name,
        "_id": cat_id,
        "doc": {"disabled": True}
    })

# Execute bulk operations
if bulk_ops:
    #logging.info(json.dumps(bulk_ops, indent=2))
    helpers.bulk(es, bulk_ops)
    logging.info(f"[CAT][{file_name}]: synced {len(bulk_ops)} category documents.")
else:
    logging.info(f"[CAT][{file_name}]: no changes detected")
