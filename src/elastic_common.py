from elasticsearch import Elasticsearch, helpers
import logger
import logging
import json

def fetch_all_categories(es, index_name, tenant_id, supplier_id):
    categories = []

    docs = helpers.scan(es, index=index_name, 
                            query={
                                "query": {
                                    "bool": {
                                        "must": [
                                            {"term": { "tenant_id.keyword": tenant_id }},
                                            {"term": { "supplier_id.keyword": supplier_id }}
                                        ]
                                    }
                                }
                            })

    for doc in docs:
        categories.append({
            "level1": doc["_source"].get("level1"),
            "level2": doc["_source"].get("level2"),
            "level3": doc["_source"].get("level3"),
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
            category_lookup[level3_value] = cat
        elif level2_value:
            category_lookup[level2_value] = cat
        elif level1_value:
            category_lookup[level1_value] = cat

    return category_lookup


def get_category_level_name(category_id, categories_map, else_category_map=None):
    
    if category_id is None:
        return None

    if category_id not in categories_map and else_category_map is not None:
        category_level = else_category_map.get(category_id)
    else:
        category_level = categories_map.get(category_id)
    
    description = category_level["description"] if category_level is not None else None
        
    return description

    
def find_category_hierarchy(category_id, categories_map, else_category_map=None):
    
    category = None

    if category_id is not None:
        category = categories_map.get(category_id)
        
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
