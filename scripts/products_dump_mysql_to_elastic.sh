#!/bin/sh

TENANT="$1"

BASE_PATH="$2"


echo "$(date '+%Y-%m-%d %H:%M:%S') Starting products import from mysql for tenant $TENANT"

TENANT_LOWER="$(echo $TENANT | tr '[A-Z]' '[a-z]')"

ENV_FILE="${BASE_PATH}/scripts/.env_${TENANT_LOWER}"

if [ ! -f "$ENV_FILE" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') Error: $ENV_FILE not found."
  exit 1
fi

. "$ENV_FILE"

# Check for required arguments
if [ -z "$1" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') Usage: $0 TENANT_ID BASE_PATH"
  exit 1
fi

echo "$(date '+%Y-%m-%d %H:%M:%S')     Import products from mysql for tenant $TENANT"

/usr/local/bin/python3 "${BASE_PATH}/src/products_dump_mysql_to_elastic.py" $TENANT

echo "$(date '+%Y-%m-%d %H:%M:%S') End products import from mysql for tenant $TENANT"
