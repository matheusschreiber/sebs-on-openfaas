#!/bin/bash
set -euo pipefail

# GETTING THE PARAMETERS FROM THE COMMAND LINE OPTIONS

usage() {
    cat <<'EOF'
Usage: run_benchmark.sh --function NAME --benchmark NAME --benchmark-dir PATH --storage MODE

Required options:
  --function NAME       Function name from stack.yaml
  --benchmark NAME      SeBS benchmark identifier, e.g. 130.crud-api
  --benchmark-dir PATH  Path under serverless-benchmarks/benchmarks/
  --storage MODE        One of: none, object, nosql
  -h, --help            Show this help
EOF
}

FUNCTION=""
BENCHMARK=""
BENCHMARK_DIR=""
STORAGE=""
VM_SSH=""

while (($#)); do
    case "$1" in
        --function)
            [[ $# -ge 2 ]] || { printf 'ERROR: --function requires a value.\n' >&2; exit 2; }
            FUNCTION="$2"
            shift 2
            ;;
        --benchmark)
            [[ $# -ge 2 ]] || { printf 'ERROR: --benchmark requires a value.\n' >&2; exit 2; }
            BENCHMARK="$2"
            shift 2
            ;;
        --benchmark-dir)
            [[ $# -ge 2 ]] || { printf 'ERROR: --benchmark-dir requires a value.\n' >&2; exit 2; }
            BENCHMARK_DIR="$2"
            shift 2
            ;;
        --storage)
            [[ $# -ge 2 ]] || { printf 'ERROR: --storage requires a value.\n' >&2; exit 2; }
            STORAGE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$FUNCTION" || -z "$BENCHMARK" || -z "$BENCHMARK_DIR" || -z "$STORAGE" ]]; then
    printf 'ERROR: --function, --benchmark, --benchmark-dir, and --storage are required.\n' >&2
    usage >&2
    exit 2
fi

case "$STORAGE" in
    none|object|nosql) ;;
    *)
        printf 'ERROR: --storage must be one of: none, object, nosql (got %s).\n' "$STORAGE" >&2
        exit 2
        ;;
esac

# SEBS CONFIGURATION AND PYTHON VENV

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
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
: "${SSH_PORT:?Set SSH_PORT in .env}"
: "${SSH_TARGET:?Set SSH_TARGET in .env}"
: "${SSH_KEY_FILE:?Set SSH_KEY_FILE in .env}"
: "${VBOX_NETWORK_MODE:?Set VBOX_NETWORK_MODE in .env}"
: "${DOCKERHUB_USERNAME:?Set DOCKERHUB_USERNAME in .env}"
SEBS="$SEBS_DIR"
OUTPUT_DIR=input-output/io-$FUNCTION
mkdir -p "$OUTPUT_DIR"

if [[ "${VIRTUAL_ENV:-}" != "$SEBS/python-venv" ]]; then
    source "$SEBS/python-venv/bin/activate"
fi

# LOAD STORAGE SETTINGS AND ENV VARIABLES

STORAGE_CONFIG="$SEBS/storage.json"
ACCESS_KEY=""
SECRET_KEY=""
STORAGE_ADDR=""
STORAGE_LOCAL=""
NOSQL_ADDR=""
NOSQL_LOCAL=""

case "$STORAGE" in
    none)
        ;;
    object)
        if [[ ! -f "$STORAGE_CONFIG" ]]; then
            printf 'ERROR: storage configuration not found: %s\n' "$STORAGE_CONFIG" >&2
            exit 1
        fi
        read -r ACCESS_KEY SECRET_KEY STORAGE_ADDR STORAGE_LOCAL < <(python3 - "$STORAGE_CONFIG" <<'PY'
import json, sys

data = json.load(open(sys.argv[1]))
obj = data["object"][data["object"]["type"]]
print(obj["access_key"], obj["secret_key"], obj.get("external_address") or obj["address"], "127.0.0.1:%s" % obj["mapped_port"])
PY
        )
        ;;
    nosql)
        if [[ ! -f "$STORAGE_CONFIG" ]]; then
            printf 'ERROR: storage configuration not found: %s\n' "$STORAGE_CONFIG" >&2
            exit 1
        fi
        read -r NOSQL_ADDR NOSQL_LOCAL < <(python3 - "$STORAGE_CONFIG" <<'PY'
import json, sys

data = json.load(open(sys.argv[1]))
config = data["nosql"][data["nosql"]["type"]]
print(config.get("external_address") or config["address"], "127.0.0.1:%s" % config["mapped_port"])
PY
        )
        ;;
    *)
        printf 'ERROR: STORAGE must be one of: none, object, nosql (got %s).\n' "$STORAGE" >&2
        exit 2
        ;;
esac

# RUNNING INPUT VALIDATION

# manual check
# python3 run.py --gateway http://127.0.0.1:8080 --function sebs-411-image-recognition --benchmark 411-image-recognition --mode cold -n 1 --input input-output/io-411-image-recognition/input.json --out input-output/io-411-image-recognition/results.jsonl --input-module ../serverless-benchmarks/benchmarks/400.inference/411.image-recognition/input.py

INPUT_ARGS=(--size test)
case "$STORAGE" in
    object)
        INPUT_ARGS+=(--endpoint "$STORAGE_LOCAL" --access-key "$ACCESS_KEY" --secret-key "$SECRET_KEY")
        ;;
    nosql)
        INPUT_ARGS+=(--nosql-endpoint "$NOSQL_LOCAL")
        ;;
esac

# CREATING INPUT

python3 make_input.py "$SEBS/benchmarks/$BENCHMARK_DIR" \
    --data-dir $SEBS/benchmarks-data/$BENCHMARK_DIR \
    "${INPUT_ARGS[@]}"

mv input.json "$OUTPUT_DIR/input.json"

# VALIDATING NEW INPUT 

printf -v VM_SSH -- '-p %q -i %q %q' "$SSH_PORT" "$SSH_KEY_FILE" "$SSH_TARGET"
B=(--gateway "$GATEWAY_URL" --function "$FUNCTION" --benchmark "$BENCHMARK" --vm-ssh "$VM_SSH")
VALIDATION_RESULTS="$OUTPUT_DIR/validation-results.jsonl"
rm -f "$VALIDATION_RESULTS"
trap 'rm -f "$VALIDATION_RESULTS"' EXIT

if validation_output=$(python3 run.py "${B[@]}" --mode warm -n 3 \
    --input "$OUTPUT_DIR/input.json" \
    --data-dir "$SEBS/benchmarks-data/$BENCHMARK_DIR" \
    --input-module "$SEBS/benchmarks/$BENCHMARK_DIR/input.py" \
    --out "$VALIDATION_RESULTS" 2>&1); then
    printf '%s\n' "$validation_output"
else
    validation_status=$?
    printf 'ERROR: benchmark validation command failed (exit %s):\n%s\n' \
        "$validation_status" "$validation_output" >&2
    exit "$validation_status"
fi

if [[ "$validation_output" != *"validate_output: OK"* ]]; then
    printf 'ERROR: output validation failed for %s. Validation output:\n%s\n' \
        "$FUNCTION" "$validation_output" >&2
    exit 1
fi

rm -f "$VALIDATION_RESULTS"
trap - EXIT

# RUNNING BENCHMARK MULTIPLE TIMES

rm -f "$OUTPUT_DIR/results.jsonl" && touch "$OUTPUT_DIR/results.jsonl"
python3 run.py "${B[@]}" --mode warm  -n 200 --input "$OUTPUT_DIR/input.json" --out "$OUTPUT_DIR/results.jsonl"
python3 run.py "${B[@]}" --mode cold  -n 50  --input "$OUTPUT_DIR/input.json" --out "$OUTPUT_DIR/results.jsonl"
python3 run.py "${B[@]}" --mode burst -n 20  --input "$OUTPUT_DIR/input.json" --rounds 10 --out "$OUTPUT_DIR/results.jsonl"

# PLOTING RESULTS

# derive the python compiler label from the function image for the plot titles
IMAGE_TAG="$DOCKERHUB_USERNAME/$FUNCTION:latest"
PYTHON_VV="$(docker run --rm "$IMAGE_TAG" python3 -VV 2>/dev/null || true)"
PYTHON_FPROCESS="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE_TAG" 2>/dev/null | grep '^fprocess=' || true)"
PYTHON_COMPILER="$(PYTHON_VV="$PYTHON_VV" PYTHON_FPROCESS="$PYTHON_FPROCESS" python3 - <<'PY'
import os, re

vv = os.environ.get("PYTHON_VV", "")
fprocess = os.environ.get("PYTHON_FPROCESS", "")
pypy = re.search(r"PyPy \S+", vv)
cpython = re.search(r"Python \S+", vv)
if pypy:
    label = pypy.group(0) + (" (%s)" % cpython.group(0) if cpython else "")
elif cpython:
    label = "C" + cpython.group(0)
else:
    label = ""
if "--jit off" in fprocess:
    label = (label + " [JIT off]").strip()
print(label)
PY
)"

python3 analyze.py "$OUTPUT_DIR/results.jsonl" --outdir "$OUTPUT_DIR/plots" --python-compiler "$PYTHON_COMPILER"

# REGISTERING THE ENVIRONMENT

OUT=env.log
case "$STORAGE" in
    none) STORAGE_DESCRIPTION="storage: none" ;;
    object) STORAGE_DESCRIPTION="storage: object at $STORAGE_ADDR" ;;
    nosql) STORAGE_DESCRIPTION="storage: NoSQL at $NOSQL_ADDR" ;;
esac
 
{
  echo "== date (UTC)";  date -u
  echo "== faas-cli";    faas-cli version
    echo "== host"
    uname -a
    nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || true
    free -h 2>/dev/null || true
  echo "== VM"
    ssh -p "$SSH_PORT" -i "$SSH_KEY_FILE" "$SSH_TARGET" \
      'uname -a; nproc; free -h; lscpu | head -15'
  echo "== setup"
  echo "VirtualBox network mode: $VBOX_NETWORK_MODE"
    echo "Template: python3-http-debian; image: $IMAGE_TAG; $STORAGE_DESCRIPTION"
  echo "== python compiler (function image)"
    docker run --rm "$IMAGE_TAG" python3 -VV 2>&1 \
      || echo "WARN: could not query python version from $IMAGE_TAG"
  echo "== SeBS commit"; git -C "$SEBS" rev-parse HEAD
  echo "== faasd / containerd versions: run 'sudo faasd version' and 'sudo ctr version | head -5' on the VM and paste the output below"
} > "$OUT"

mv "$OUT" "$OUTPUT_DIR/$OUT"
 
cat "$OUTPUT_DIR/$OUT"