#!/bin/bash
set -euo pipefail

bash run_benchmark.sh \
    --function sebs-110-dynamic-html-cpython \
    --benchmark 110.dynamic-html \
    --benchmark-dir 100.webapps/110.dynamic-html \
    --storage none 

bash run_benchmark.sh \
    --function sebs-110-dynamic-html-pypy-jit \
    --benchmark 110.dynamic-html \
    --benchmark-dir 100.webapps/110.dynamic-html \
    --storage none 

bash run_benchmark.sh \
    --function sebs-110-dynamic-html-pypy-nojit \
    --benchmark 110.dynamic-html \
    --benchmark-dir 100.webapps/110.dynamic-html \
    --storage none 

# COMPARISON PLOTS ACROSS THE RUNS ABOVE

python3 compare.py \
    --run "CPython" input-output/io-sebs-110-dynamic-html-cpython/results.jsonl \
    --run "PyPy (JIT)" input-output/io-sebs-110-dynamic-html-pypy-jit/results.jsonl \
    --run "PyPy (no JIT)" input-output/io-sebs-110-dynamic-html-pypy-nojit/results.jsonl \
    --outdir input-output/comparison-110-dynamic-html