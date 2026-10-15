from elasticsearch import Elasticsearch, helpers
import logger
import logging
import json
import os

def fetch_all_categories(es, index_name, tenant_id, supplier_id):
    tenant_id = tenant_id.lower()
    categories = []

    query={
        "query": {
            "bool": {
                "must": [
                    {"term": { "tenant_id.keyword": tenant_id }},
                    {"term": { "supplier_id.keyword": supplier_id }}
                ]
            }
        }
    }

    docs = helpers.scan(es, index=index_name, query=query)

    for doc in docs:
        categories.append({
            "level1": doc["_source"].get("level1"),
            "level2": doc["_source"].get("level2"),
            "level3": doc["_source"].get("level3"),
            "disabled": doc["_source"].get("disabled", False),
            "description": doc["_source"].get("description", {})
        })

    return categories


def generate_categories_map(categories):
    category_lookup = {}

    for cat in categories:
        level1_value = cat["level1"]
        level2_value = cat["level2"]
        level3_value = cat["level3"]

        if level3_value:
            key = str(level3_value)
        elif level2_value:
            key = str(level2_value)
        elif level1_value:
            key = str(level1_value)
        else:
            continue

        existing = category_lookup.get(key)
        # Sentinel/disabled category docs (e.g. 999999-1901-999999) can share
        # a key with real ones (e.g. 19-1901): never let a disabled doc
        # shadow an enabled one.
        if existing is not None and not existing.get("disabled", False) and cat.get("disabled", False):
            continue

        category_lookup[key] = cat

    return category_lookup


def get_category_level_name(category_id, categories_map, else_category_map=None):

    if category_id is None:
        return None

    category_id = str(category_id)

    if category_id not in categories_map and else_category_map is not None:
        category_level = else_category_map.get(category_id)
    else:
        category_level = categories_map.get(category_id)

    description = category_level["description"] if category_level is not None else None

    return description


def find_category_hierarchy(category_id, categories_map, else_category_map=None):

    category = None

    if category_id is not None:
        # Map keys are strings (they come from ES documents); ids coming from
        # MySQL may be ints, so normalize before looking up.
        category = categories_map.get(str(category_id))

    if category is None:
        logging.error(f"category_id {category_id} not found in categories_map")
        return None  # category not found

    return {
        "level1": category.get("level1"),
        "level2": category.get("level2"),
        "level3": category.get("level3"),
        "level1Name": get_category_level_name(category.get("level1"), categories_map, else_category_map),
        "level2Name": get_category_level_name(category.get("level2"), categories_map, else_category_map),
        "level3Name": get_category_level_name(category.get("level3"), categories_map, else_category_map),
    }


def config_logging(log_file="error_log.log"):
    logger.setup_logger(log_file)


def write_import_summary(text):
    """
    Writes a one-line result summary of the current import to the file given by
    the IMPORT_SUMMARY_FILE env var (set by the shell wrapper that maintains the
    daily import log). No-op when the variable is not set.
    """
    path = os.environ.get("IMPORT_SUMMARY_FILE")
    if not path:
        return
    try:
        with open(path, "w") as f:
            f.write(" ".join(str(text).split()))
    except OSError as e:
        logging.warning(f"Could not write import summary to {path}: {e}")
