#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STACK_FILE="$SCRIPT_DIR/stack.yaml"
ENV_FILE="$SCRIPT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    printf 'ERROR: %s is missing. Copy .env.example to .env and configure it.\n' "$ENV_FILE" >&2
    exit 1
fi
set -a
source "$ENV_FILE"
set +a
: "${SEBS_DIR:?Set SEBS_DIR in .env}"
: "${GATEWAY_URL:?Set GATEWAY_URL in .env}"
SEBS="$SEBS_DIR"

# CHECKING OPENFAAS CLI AND STACK FILE

if ! command -v faas-cli >/dev/null 2>&1; then
	printf 'ERROR: faas-cli is not installed or not on PATH.\n' >&2
	exit 127
fi

if [[ ! -f "$STACK_FILE" ]]; then
	printf 'ERROR: OpenFaaS stack file not found: %s\n' "$STACK_FILE" >&2
	exit 1
fi

# CHECKING STORAGE SERVICES

cd "$SEBS"
printf "Checking storage services..."
if [[ -f storage.json ]] \
    && read -r OBJECT_PORT NOSQL_PORT < <(python3 - storage.json <<'PY'
import json, sys

data = json.load(open(sys.argv[1]))
obj = data["object"][data["object"]["type"]]
nosql = data["nosql"][data["nosql"]["type"]]
assert obj["access_key"] and obj["secret_key"]
print(obj["mapped_port"], nosql["mapped_port"])
PY
    ) \
    && curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$OBJECT_PORT/health" >/dev/null \
    && curl --fail --silent --show-error --max-time 3 "http://127.0.0.1:$NOSQL_PORT/" >/dev/null; then
    printf 'Reusing running object and NoSQL storage from %s/storage.json\n' "$SEBS"
else
    printf 'Starting object and NoSQL storage...\n'
    python3 -m sebs.cli storage start all configs/storage.json --output-json storage.json
fi
read -r ACCESS_KEY SECRET_KEY STORAGE_ADDR STORAGE_LOCAL NOSQL_ADDR < <(python3 - storage.json <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
c = data["object"]; c = c[c["type"]]
n = data["nosql"]; n = n[n["type"]]
print(c["access_key"], c["secret_key"], c.get("external_address") or c["address"], "127.0.0.1:%s" % c["mapped_port"], n.get("external_address") or n["address"])
PY
)

# UPDATING stack.yml WITH STORAGE CREDENTIALS

cd "$SCRIPT_DIR"

sed -i -E \
    -e "s|^([[:space:]]*gateway:[[:space:]]*).*|\1\"$GATEWAY_URL\"|" \
    -e "s|^([[:space:]]*MINIO_STORAGE_ACCESS_KEY:[[:space:]]*).*|\1\"$ACCESS_KEY\"|" \
    -e "s|^([[:space:]]*MINIO_STORAGE_SECRET_KEY:[[:space:]]*).*|\1\"$SECRET_KEY\"|" \
    -e "s|^([[:space:]]*MINIO_STORAGE_CONNECTION_URL:[[:space:]]*).*|\1\"$STORAGE_ADDR\"|" \
    -e "s|^([[:space:]]*NOSQL_STORAGE_ENDPOINT:[[:space:]]*).*|\1\"$NOSQL_ADDR\"|" \
    "$STACK_FILE"

# CHECKING OPENFAAS FUNCTION UPLOADED

faas-cli up -f "$STACK_FILE"