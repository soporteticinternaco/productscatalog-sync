import json
import sys
from elasticsearch import Elasticsearch
import os

# Configuration (fallback to env vars if not provided)
ELASTIC_SEARCH_URL = os.environ.get("ELASTIC_SEARCH_URL", "http://elastic:KKK-ic99@192.168.17.80:9200")

def update_index(index_name):
    es = Elasticsearch([ELASTIC_SEARCH_URL])
    
    print(f"Updating index: {index_name}")
    
    if not es.indices.exists(index=index_name):
        print(f"Index {index_name} does not exist. Skipping.")
        return

    # 1. Close the index to update settings
    print("Closing index...")
    es.indices.close(index=index_name)
    
    # 2. Update settings (add analyzers)
    print("Updating settings (analyzers)...")
    settings_body = {
        "analysis": {
            "char_filter": {
                "iberian_normalization": {
                    "type": "mapping",
                    "mappings": [
                        "v => b",
                        "V => b",
                        "h => ",
                        "H => "
                    ]
                }
            },
            "analyzer": {
                "alphanumeric": {
                    "tokenizer": "keyword",
                    "char_filter": ["iberian_normalization"],
                    "filter": ["lowercase", "asciifolding", "alphanumeric_filter"]
                },
                "phonetic_analyzer": {
                    "tokenizer": "standard",
                    "filter": ["lowercase", "iberian_phonetic_filter"]
                },
                "first_word_analyzer": {
                    "tokenizer": "standard",
                    "char_filter": ["iberian_normalization"],
                    "filter": ["lowercase", "asciifolding", "first_word_limit"]
                },
                "iberian_standard": {
                    "tokenizer": "standard",
                    "char_filter": ["iberian_normalization"],
                    "filter": ["lowercase", "asciifolding"]
                }
            },
            "filter": {
                "alphanumeric_filter": {
                    "type": "pattern_replace",
                    "pattern": "[^a-z0-9]",
                    "replacement": ""
                },
                "iberian_phonetic_filter": {
                    "type": "phonetic",
                    "encoder": "beider_morse",
                    "languageset": ["spanish", "portuguese"],
                    "replace": False
                },
                "first_word_limit": {
                    "type": "limit",
                    "max_token_count": 1
                }
            }
        }
    }
    es.indices.put_settings(index=index_name, body=settings_body)
    
    # 3. Open the index
    print("Opening index...")
    es.indices.open(index=index_name)
    
    # 4. Update mapping (add subfields)
    print("Updating mapping (subfields)...")
    if index_name == "productscatalog-elsabio-products":
        mapping_body = {
            "properties": {
                "supplier_name": {
                    "type": "text",
                    "fields": {
                        "phonetic": {"type": "text", "analyzer": "phonetic_analyzer"}
                    }
                },
                "description": {
                    "properties": {
                        "es": {
                            "type": "text",
                            "analyzer": "iberian_standard",
                            "search_analyzer": "my_search_analyzer_es",
                            "fields": {
                                "phonetic": {"type": "text", "analyzer": "phonetic_analyzer"}
                            }
                        },
                        "pt": {
                            "type": "text",
                            "analyzer": "iberian_standard",
                            "search_analyzer": "my_search_analyzer_pt",
                            "fields": {
                                "phonetic": {"type": "text", "analyzer": "phonetic_analyzer"}
                            }
                        }
                    }
                }
            }
        }
    else:
        mapping_body = {
            "properties": {
                "supplier_name": {
                    "type": "text",
                    "fields": {
                        "phonetic": {"type": "text", "analyzer": "phonetic_analyzer"}
                    }
                },
                "description": {
                    "properties": {
                        "es": {
                            "type": "text",
                            "fields": {
                                "phonetic": {"type": "text", "analyzer": "phonetic_analyzer"}
                            }
                        },
                        "pt": {
                            "type": "text",
                            "fields": {
                                "phonetic": {"type": "text", "analyzer": "phonetic_analyzer"}
                            }
                        }
                    }
                }
            }
        }
    es.indices.put_mapping(index=index_name, body=mapping_body)
    
    print(f"✅ Index {index_name} updated successfully.")

if __name__ == "__main__":
    indices = [
        "productscatalog-elsabio-products",
        "productscatalog-internaco-products",
        "productscatalog-sa92-products",
        "productscatalog-maxrain-products",
        "productscatalog-ipmax-products"
    ]
    
    for idx in indices:
        try:
            update_index(idx)
        except Exception as e:
            print(f"Error updating index {idx}: {str(e)}")
