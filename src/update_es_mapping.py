from elasticsearch import Elasticsearch
import os

# Configuration (fallback to env vars if not provided)
ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL", "http://elastic:KKK-ic99@192.168.17.80:9200")

# New field added to every product index. Indexed as a nested array of
# {lang, name, value} entries so facet aggregations can enumerate characteristic
# names and their value distributions in a single query. Characteristic names
# differ per language, so each language contributes its own entries tagged with
# `lang`; facets are aggregated and filtered by adding a term filter on
# characteristics.lang inside the nested query.
MAPPING_BODY = {
    "properties": {
        "characteristics": {
            "type": "nested",
            "properties": {
                "lang": {"type": "keyword"},
                "name": {"type": "keyword"},
                "value": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            },
        }
    }
}


def update_index(index_name):
    es = Elasticsearch([ELASTIC_SEARCH_URL])

    print(f"Updating index: {index_name}")

    if not es.indices.exists(index=index_name):
        print(f"Index {index_name} does not exist. Skipping.")
        return

    es.indices.put_mapping(index=index_name, body=MAPPING_BODY)

    print(f"✅ Index {index_name} updated successfully.")


if __name__ == "__main__":
    indices = [
        "productscatalog-elsabio-products-v2"
        #"productscatalog-internaco-products-v2",
        #"productscatalog-sa92-products-v2",
        #"productscatalog-maxrain-products-v2",
        #"productscatalog-ipmax-products-v2",
    ]

    for idx in indices:
        try:
            update_index(idx)
        except Exception as e:
            print(f"Error updating index {idx}: {str(e)}")
