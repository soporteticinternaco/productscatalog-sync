from elasticsearch import Elasticsearch, helpers
import logging

def fetch_all_categories(es, tenant_id, supplier_id):
    categories = []

    for doc in helpers.scan(es, index="productscatalog-categories", 
                            query={
                                "query": {
                                    "bool": {
                                        "must": [
                                            {"term": { "tenant_id": tenant_id }},
                                            {"term": { "supplier_id": supplier_id }}
                                        ]
                                    }
                                }
                            }):

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

        if level1_value and not level2_value and not level3_value:
            category_lookup[level1_value] = cat
        elif level1_value and level2_value and not level3_value:
            category_lookup[level2_value] = cat
        elif level1_value and level2_value and level3_value:
            category_lookup[level3_value] = cat

    return category_lookup


def get_category_level_name(category_id, categories_map):
    
    if category_id is None:
        return None
    
    category_level = categories_map[category_id]
    description = category_level["description"] if category_level is not None else None
        
    return description

def find_category_hierarchy(category_id, categories_map):
    category = categories_map.get(category_id)
    if not category:
        return None  # category not found

    return {
        "level1": category.get("level1"),
        "level2": category.get("level2"),
        "level3": category.get("level3"),
        "level1Name": get_category_level_name(category.get("level1"), categories_map),
        "level2Name": get_category_level_name(category.get("level2"), categories_map),
        "level3Name": get_category_level_name(category.get("level3"), categories_map),
    }
    
def config_logging():
    logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)