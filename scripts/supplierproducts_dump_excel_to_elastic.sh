#!/bin/sh

TENANT="$1"

BASE_DATA_PATH="$2"

BASE_PATH="$3"

# Daily import log (one file per run). Defaults to <BASE_DATA_PATH>/logs, which is already
# mapped to the host in docker-compose; set EDP_LOG_PATH to use another folder.
LOG_PATH="${EDP_LOG_PATH:-$BASE_DATA_PATH/logs}"
mkdir -p "$LOG_PATH" || echo "$(date '+%Y-%m-%d %H:%M:%S') Warning: cannot create $LOG_PATH, daily log disabled"
DAILY_LOG="$LOG_PATH/edp_import_$(date '+%Y-%m-%d_%H%M').log"
SUMMARY_FILE="$(mktemp)"
export IMPORT_SUMMARY_FILE="$SUMMARY_FILE"
trap 'rm -f "$SUMMARY_FILE"' EXIT

# Appends a line to the daily log
daily_log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$DAILY_LOG"
}

# Runs one import and records file, result and final location in the daily log
# $1: kind (CATEGORIES|PRODUCTS)  $2: python script  $3: file
import_file() {
  kind="$1"; script="$2"; file="$3"
  name="$(basename "$file")"
  : > "$SUMMARY_FILE"
  daily_log "[$kind] SELECTED $name"
  /usr/local/bin/python3 "$BASE_PATH/src/$script" ELSABIO "$file"
  rc=$?
  detail="$(cat "$SUMMARY_FILE" 2>/dev/null)"
  if [ $rc -eq 0 ]; then
    if mv "$file" "$PROCESSED_PATH"; then
      daily_log "[$kind] $name RESULT=OK ${detail:+($detail) }LOCATION=old (moved from pending)"
    else
      daily_log "[$kind] $name RESULT=OK ${detail:+($detail) }LOCATION=pending (move to old FAILED)"
    fi
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') Error processing $file, skipping move."
    daily_log "[$kind] $name RESULT=ERROR(exit $rc) ${detail:+($detail) }LOCATION=pending (not moved)"
  fi
}


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

daily_log "===== START edp import tenant=$TENANT pending=$PENDING_PATH ====="

echo "$(date '+%Y-%m-%d %H:%M:%S')     Process excel categories files for tenant $TENANT"

# Process *categories*.xlsx files
for file in "$PENDING_PATH/"*categories*.xlsx; do
  if [ -f "$file" ]; then
    import_file CATEGORIES categories_dump_excel_2_elastic.py "$file"
  fi
done

echo "$(date '+%Y-%m-%d %H:%M:%S')     Import supplier products excel files import for tenant $TENANT"

# Process all *.xlsx files except those whose name contains "categories"
for file in "$PENDING_PATH/"*.xlsx; do
  case "$(basename "$file")" in *categories*) continue ;; esac
  if [ -f "$file" ]; then
    import_file PRODUCTS supplierproducts_dump_excel_2_elastic.py "$file"
  fi
done

daily_log "===== END edp import tenant=$TENANT ====="

echo "$(date '+%Y-%m-%d %H:%M:%S') End supplier products import from excel for tenant $TENANT"
