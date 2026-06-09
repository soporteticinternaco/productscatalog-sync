#!/bin/sh

TENANT="$1"

BASE_DATA_PATH="$2"

BASE_PATH="$3"


echo "$(date '+%Y-%m-%d %H:%M:%S') Starting supplier products import from excel for tenant $TENANT"

TENANT_LOWER="$(echo $TENANT | tr '[A-Z]' '[a-z]')"

ENV_FILE="${BASE_PATH}/scripts/.env_${TENANT_LOWER}"

if [ ! -f "$ENV_FILE" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') Error: $ENV_FILE not found."
  exit 1
fi

. "$ENV_FILE"


# Check for required arguments
if [ -z "$1" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') Usage: $0 TENANT_ID BASE_DATA_PATH [BASE_SRC_PATH]"
  exit 1
fi

PENDING_PATH="$BASE_DATA_PATH/pending"
PROCESSED_PATH="$BASE_DATA_PATH/old"


echo "$(date '+%Y-%m-%d %H:%M:%S')     Process excel categories files for tenant $TENANT"

# Process *categories*.xlsx files
for file in "$PENDING_PATH/"*categories*.xlsx; do
  if [ -f "$file" ]; then
    /usr/local/bin/python3 "$BASE_PATH"/src/categories_dump_excel_2_elastic.py ELSABIO "$file"
    if [ $? -eq 0 ]; then
      mv "$file" "$PROCESSED_PATH"
    else
      echo "$(date '+%Y-%m-%d %H:%M:%S') Error processing $file, skipping move."
    fi
  fi
done

echo "$(date '+%Y-%m-%d %H:%M:%S')     Import supplier products excel files import for tenant $TENANT"

# Process *products*.xlsx files
for file in "$PENDING_PATH/"*products*.xlsx; do
  if [ -f "$file" ]; then
    /usr/local/bin/python3 "$BASE_PATH"/src/supplierproducts_dump_excel_2_elastic.py ELSABIO "$file"
    if [ $? -eq 0 ]; then
      mv "$file" "$PROCESSED_PATH"
    else
      echo "$(date '+%Y-%m-%d %H:%M:%S') Error processing $file, skipping move."
    fi
  fi
done


echo "$(date '+%Y-%m-%d %H:%M:%S') End supplier products import from excel for tenant $TENANT"
