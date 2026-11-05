SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
	printf 'ERROR: %s is missing. Copy .env.example to .env and configure it.\n' "$ENV_FILE" >&2
	exit 1
fi
set -a
source "$ENV_FILE"
set +a
: "${SEBS_DIR:?Set SEBS_DIR in .env}"
SEBS="$SEBS_DIR"
cd "$SEBS"
python3 -m sebs.cli storage stop all storage.json