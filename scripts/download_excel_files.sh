#!/bin/sh

TENANT="ELSABIO"

export BASE_DATA_PATH="$1"

BASE_PATH="$2"


echo "Starting supplier excel files download for tenant $TENANT"

TENANT_LOWER="$(echo $TENANT | tr '[A-Z]' '[a-z]')"

ENV_FILE="${BASE_PATH}/scripts/.env_${TENANT_LOWER}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found."
  exit 1
fi

. "$ENV_FILE"

echo "1111 ${EXCEL_FILES_FTP_LOCAL_PATH}"

/usr/local/bin/python3 "${BASE_PATH}/src/download_excel_files.py"