#!/bin/bash
set -euo pipefail

VALID_STORAGE="none object nosql all"

run_comparison() {
    local benchmark_id="$1"
    local benchmark_name="$2"
    local benchmark_dir="$3"
    local storage="$4"
    local prefix="sebs-${benchmark_id}"

    if [[ ! " $VALID_STORAGE " == *" $storage "* ]]; then
        printf 'ERROR: invalid storage "%s" for %s (use one of: %s)\n' "$storage" "$benchmark_name" "$VALID_STORAGE" >&2
        exit 1
    fi

    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    STACK_FILE="$SCRIPT_DIR/sebs-${benchmark_id}/stack.yaml"
    if [[ ! -f "$STACK_FILE" ]]; then
        printf 'ERROR: OpenFaaS stack file not found: %s\n' "$STACK_FILE" >&2
        exit 1
    fi
    faas-cli up -f "$STACK_FILE"

    bash run_benchmark.sh \
        --function "${prefix}-cpython" \
        --benchmark "${benchmark_name}" \
        --benchmark-dir "${benchmark_dir}" \
        --storage "${storage}"

    bash run_benchmark.sh \
        --function "${prefix}-pypy-jit" \
        --benchmark "${benchmark_name}" \
        --benchmark-dir "${benchmark_dir}" \
        --storage "${storage}"

    bash run_benchmark.sh \
        --function "${prefix}-pypy-nojit" \
        --benchmark "${benchmark_name}" \
        --benchmark-dir "${benchmark_dir}" \
        --storage "${storage}"

    python3 compare.py \
        --run "CPython" "$SCRIPT_DIR/input-output/io-${prefix}-cpython/results.jsonl" \
        --run "PyPy (JIT)" "$SCRIPT_DIR/input-output/io-${prefix}-pypy-jit/results.jsonl" \
        --run "PyPy (no JIT)" "$SCRIPT_DIR/input-output/io-${prefix}-pypy-nojit/results.jsonl" \
        --outdir "$SCRIPT_DIR/input-output/comparison-${benchmark_id}"
}

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <benchmark>"
    exit 1
fi

case "$1" in
    sleep)
        run_comparison \
            "010-sleep" \
            "010.sleep" \
            "000.microbenchmarks/010.sleep" \
            "none"
        ;;

    jit-kernel)
        run_comparison \
            "090-jit-kernel" \
            "090.jit-kernel" \
            "000.microbenchmarks/090.jit-kernel" \
            "object"
        ;;

    dynamic-html)
        run_comparison \
            "110-dynamic-html" \
            "110.dynamic-html" \
            "100.webapps/110.dynamic-html" \
            "none"
        ;;

    uploader)
        run_comparison \
            "120-uploader" \
            "120.uploader" \
            "100.webapps/120.uploader" \
            "object"
        ;;

    crud-api)
        run_comparison \
            "130-crud-api" \
            "130.crud-api" \
            "100.webapps/130.crud-api" \
            "nosql"
        ;;

    thumbnailer)
        run_comparison \
            "210-thumbnailer" \
            "210.thumbnailer" \
            "200.multimedia/210.thumbnailer" \
            "object"
        ;;

    video-processing)
        run_comparison \
            "220-video-processing" \
            "220.video-processing" \
            "200.multimedia/220.video-processing" \
            "object"
        ;;

    compression)
        run_comparison \
            "311-compression" \
            "311.compression" \
            "300.utilities/311.compression" \
            "object"
        ;;

    image-recognition)
        run_comparison \
            "411-image-recognition" \
            "411.image-recognition" \
            "400.inference/411.image-recognition" \
            "object"
        ;;

    graph-pagerank)
        run_comparison \
            "501-graph-pagerank" \
            "501.graph-pagerank" \
            "500.scientific/501.graph-pagerank" \
            "none"
        ;;

    graph-mst)
        run_comparison \
            "502-graph-mst" \
            "502.graph-mst" \
            "500.scientific/502.graph-mst" \
            "none"
        ;;

    graph-bfs)
        run_comparison \
            "503-graph-bfs" \
            "503.graph-bfs" \
            "500.scientific/503.graph-bfs" \
            "none"
        ;;

    dna-visualisation)
        run_comparison \
            "504-dna-visualisation" \
            "504.dna-visualisation" \
            "500.scientific/504.dna-visualisation" \
            "object"
        ;;

    *)
        echo "Unknown benchmark: $1"
        exit 1
        ;;
esac