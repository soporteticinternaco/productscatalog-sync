#!/bin/sh

TENANT="$1"

BASE_PATH="$2"


echo "Starting supplier products import from mysql for tenant $TENANT"

TENANT_LOWER="$(echo $TENANT | tr '[A-Z]' '[a-z]')"

ENV_FILE="${BASE_PATH}/scripts/.env_${TENANT_LOWER}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found."
  exit 1
fi

. "$ENV_FILE"

echo "    Import supplier categories from mysql for tenant $TENANT"

python3 "${BASE_PATH}/src/categories_own_dump_mysql_to_elastic.py" $TENANT "${TENANT_LOWER}-own"

echo "    Import supplier products from mysql for tenant $TENANT"

python3 "${BASE_PATH}/src/supplierproducts_dump_mysql_to_elastic.py" $TENANT

