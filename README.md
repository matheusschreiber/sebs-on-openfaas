# Running SeBS benchmarks on OpenFaaS

This repository contains a small harness for running Python benchmarks from
[SeBS (Serverless Benchmark Suite)](./serverless-benchmarks/README.md) on an
OpenFaaS deployment (faasd), then recording and plotting the results.

The SeBS repository is used here as a **source of benchmark code, dependencies,
input generators, and optional storage tooling**. The scripts in
[`sebs-on-openfaas/`](./sebs-on-openfaas/) invoke the deployed function
themselves; this is not SeBS's automatic deployment/invocation pipeline.

For a longer, step-by-step walkthrough, see
[`sebs-on-openfaas-guide.md`](./sebs-on-openfaas-guide.md). This README
summarizes the workflow and explains what each stage and plot represents.

## How the pieces fit together

```text
SeBS benchmark source + input.py
               |
               +--> copy benchmark function/resources into an OpenFaaS function folder
               |
               +--> make_input.py --> input.json
                                      |
run.py --HTTP POST-------------------+--> OpenFaaS gateway --> function handler
                                                        handler --> SeBS benchmark function
                                      |
run.py <-- JSON response + timings ---+
   |
   +--> results.jsonl --> analyze.py --> summary.csv and plots/
```

For benchmarks that use object storage, there is an additional path:

```text
make_input.py --> uploads benchmark input files --> SeBS object storage
function      --> reads/writes those files through the S3-compatible API
```

The storage holds benchmark input/output files. Invocation timings and the
benchmark response are returned over HTTP and recorded in `results.jsonl`;
they are not stored in object storage.

## Prerequisites

- A running OpenFaaS gateway/faasd deployment reachable from the machine running
  `faas-cli` and `run.py`.
- `faas-cli`, Docker, and access to a container registry for building/pushing
  function images. Authenticate `faas-cli` with the gateway and Docker with the
  image registry before deployment.
- A local `.env` file configured from `.env.example`. It supplies the SeBS path,
  gateway URL, Docker Hub username, and VM SSH settings used by the scripts.
- A SeBS checkout with its Python environment, selected through `SEBS_DIR` in
  `.env`.
- Python dependencies for the harness (`requests`, `pandas`, `matplotlib`, and
  `minio`; the SeBS environment may already provide some of them).
- For benchmarks using input datasets, the relevant SeBS `benchmarks-data`
  files and any benchmark-specific dependencies.

SeBS storage runs in Docker on the machine where the SeBS CLI is run. Functions
on a separate VM/container network must be able to reach the storage's
**external** address and mapped port. Do not expose storage ports publicly
without restricting network access.

Create the ignored, machine-specific environment file before using the scripts:

```bash
cd sebs-on-openfaas
cp -n .env.example .env
# Edit .env for your SeBS checkout, gateway, Docker Hub account, and VM.
```

`input-output/` is intentionally not ignored so benchmark results can be
committed. Review generated environment logs and inputs before publishing them.

## 1. Start storage when the benchmark needs it

Check the benchmark's `config.json` / modules and `input.py` to see whether it
needs object storage or NoSQL. For example, `110.dynamic-html` and `010.sleep`
do not need object storage. A benchmark such as `210.thumbnailer` does.

From the SeBS checkout, with its environment active:

```bash
cd /path/to/serverless-benchmarks
source python-venv/bin/activate
sebs storage start object configs/storage.json --output-json storage.json
```

SeBS starts its configured S3-compatible service (RustFS in the current
configuration) and writes connection details, including credentials and
addresses, to `storage.json`. Treat that file as sensitive; do not commit or
publish it. The host-side input generator uses the host-reachable endpoint; the
function needs the address reachable from the function's network. Those may be
different addresses.

For a benchmark that uses storage, pass the host endpoint and credentials to
`make_input.py` so it can upload input files. Configure the function's
environment in `stack.yaml` with the function-reachable endpoint and the
corresponding credentials, following the benchmark wrapper's expected variable
names. For a benchmark with no storage, skip this section.

Stop storage when finished, using the generated configuration:

```bash
sebs storage stop object storage.json
```

See SeBS's [storage documentation](./serverless-benchmarks/docs/storage.md) for
object-storage/NoSQL options, addressing, and lifecycle details.

`run_benchmark.sh` takes the function, benchmark identifier, benchmark path, and
storage mode as command-line options. The storage mode can be `none`, `object`,
or `nosql`.

## 2. Copy a SeBS benchmark into an OpenFaaS function

The SeBS benchmark source and the OpenFaaS adapter have different jobs:

- The benchmark's `function.py` implements the workload. Keep it unchanged
  where possible so the computation stays comparable with SeBS.
- The OpenFaaS `handler.py` adapts the incoming HTTP event to the benchmark
  function, measures wrapper-side execution, and formats an HTTP response.
- `requirements.txt` and any accompanying files (templates, data, helper
  modules, or binaries) are needed to build/run that benchmark.

For example, to prepare `110.dynamic-html` from the SeBS checkout:

```bash
SEBS=/path/to/serverless-benchmarks
BENCHMARK="$SEBS/benchmarks/100.webapps/110.dynamic-html"
FUNCTION_DIR=sebs-on-openfaas/sebs-110-dynamic-html

cp "$BENCHMARK/python/function.py" "$FUNCTION_DIR/function.py"
cp -r "$BENCHMARK/python/templates" "$FUNCTION_DIR/templates"
```

Copy the benchmark's Python dependencies into that function folder's
`requirements.txt` as appropriate. Other benchmarks may require different
resources, dependency versions, or initialization steps; consult their
`config.json`, `python/` directory, `input.py`, and `init.sh`.

The OpenFaaS wrapper in the function folder should be adapted to the benchmark
contract. The shared example is
[`base_handler.py`](./sebs-on-openfaas/base_handler.py); the active benchmark
folders also contain their own handlers. The `python3-http` template's
[`index.py`](./sebs-on-openfaas/template/python3-http/index.py) receives the
HTTP request, constructs an event (`event.body`, headers, method, etc.), and
calls `handler.handle(event, context)`. In turn, the wrapper decodes the JSON
body and calls the benchmark's `function.handler(inputs)`.

For storage-using benchmarks, the wrapper and benchmark may also need the
appropriate SeBS storage helper and environment configuration. A NoSQL
benchmark can require table allocation and setup beyond the object-storage
input-generation script; `make_input.py` intentionally refuses benchmarks
whose `input.py` defines `allocate_nosql`.

## 3. Configure and deploy with `stack.yaml`

[`sebs-on-openfaas/stack.yaml`](./sebs-on-openfaas/stack.yaml) lists the
functions, template language, source folder, image name, memory limit, and
timeouts. Before deploying:

1. Make sure the function's `handler.py`, benchmark `function.py`,
   `requirements.txt`, and extra resources are in the folder named by
   `handler:`.
2. Set `image:` to a registry/repository you can push to.
3. Set memory and execution/read/write timeouts to suit the benchmark.
4. Add storage environment variables only for a benchmark that needs them.
   Avoid committing real credentials; use a local/private configuration or
   your deployment's secret mechanism.
5. Confirm the `gateway` URL is reachable from the command line.

Authenticate Docker with Docker Hub before building and pushing the function
images:

```bash
docker login
```

Enter your Docker Hub username and password or access token when prompted. For
private images, also configure registry credentials on the OpenFaaS host so it
can pull them.

Build, push, and deploy the functions in the stack:

```bash
cd sebs-on-openfaas
# Run once if this template is not already installed locally.
faas-cli template store pull python3-http-debian
faas-cli up -f stack.yaml
```

Smoke-test a function with its JSON input. For example:

```bash
curl -sS -X POST \
  -H 'Content-Type: application/json' \
  -d '{"username":"testname","random_len":10}' \
  http://127.0.0.1:8080/function/sebs-110-dynamic-html
```

The response should include the benchmark result and wrapper metadata such as
`begin`, `end`, `results_time`, and `is_cold`. Inspect `faas-cli logs
<function-name>` if the request fails.

## 4. Generate the benchmark input

[`make_input.py`](./sebs-on-openfaas/make_input.py) imports the selected SeBS
benchmark's `input.py` and calls its `generate_input(...)` function. It writes
the returned configuration as `input.json`; it does not fetch a universal
pre-prepared payload. Depending on the benchmark, the generator can also use
files from `benchmarks-data` and upload them to object storage.

For a benchmark without storage, such as dynamic HTML, the generated input is
small. The benchmark's input generator returns (for `test` size) values like:

```json
{"username": "testname", "random_len": 10}
```

For a storage benchmark, run the generator with the benchmark directory, data
directory, storage endpoint, and credentials from the storage setup. Example:

```bash
python3 make_input.py \
  "$SEBS/benchmarks/200.multimedia/210.thumbnailer" \
  --data-dir "$SEBS/benchmarks-data/200.multimedia/210.thumbnailer" \
  --size test \
  --endpoint 127.0.0.1:9011 \
  --access-key "$ACCESS_KEY" \
  --secret-key "$SECRET_KEY" \
  --out input.json
```

Use the actual host-reachable storage endpoint and benchmark data directory for
your environment. `run.py` then reads this JSON and POSTs it to
`/function/<function-name>`; the OpenFaaS HTTP template turns that request into
the event delivered to the handler.

## 5. Validate and run measurements

Optionally validate a successful response with the benchmark's own
`validate_output()` function:

```bash
python3 run.py \
  --gateway http://127.0.0.1:8080 \
  --function sebs-110-dynamic-html \
  --benchmark 110.dynamic-html \
  --mode warm -n 3 \
  --input input.json \
  --input-module "$SEBS/benchmarks/100.webapps/110.dynamic-html/input.py"
```

The runner appends one JSON record per invocation to `results.jsonl`:

```bash
BENCHMARK_ARGS="--gateway http://127.0.0.1:8080 \
  --function sebs-110-dynamic-html \
  --benchmark 110.dynamic-html"

python3 run.py $BENCHMARK_ARGS --mode warm  -n 200 --input input.json --out results.jsonl
python3 run.py $BENCHMARK_ARGS --mode cold  -n 50  --input input.json --out results.jsonl
python3 run.py $BENCHMARK_ARGS --mode burst -n 20 --rounds 10 --input input.json --out results.jsonl
```

Use a fresh/empty output file for a new campaign if you do not want to append to
prior measurements.

The modes mean:

- **warm:** makes one initial warm-up call, marks it as warm-up, then makes
  sequential measured calls. The analyzer excludes the warm-up.
- **cold:** before each measured call, redeploys with a changed environment
  value, waits for readiness, and pauses for the settle interval. Those
  preparation steps are not included in `client_latency_s`; the separate
  `cold_total_s` field includes deploy, wait, settle, and request time. Check
  `is_cold` in the results because redeployment may not always produce a cold
  invocation.
- **burst:** sends `-n` concurrent requests per round for `--rounds` rounds.

If cold-mode redeployment fails, inspect the `faas-cli deploy` output printed
before the Python traceback. A successful invocation does not prove deployment
permissions or image availability; deployment errors must be resolved separately.
To retry a single cold request without restarting the warm campaign, run
`run.py` with the same gateway, function, benchmark, input, and output arguments
but use `--mode cold -n 1`.

In the recorded data, `client_latency_s` is measured on the runner from just
before the HTTP POST until the response is received. `function_time_s` comes
from the handler's `results_time`, measured inside the function wrapper around
input parsing and benchmark execution. Their difference is recorded as
`overhead_s`: an estimate of time outside that measured handler interval,
including gateway/platform/network/response overhead; it is **not** a direct
gateway-only or network-only measurement.

## 6. Generate and interpret the plots

Run the analyzer from `sebs-on-openfaas`:

```bash
python3 analyze.py io-110.dynamic-html/results.jsonl --outdir io-110.dynamic-html/plots
```

It includes successful HTTP 200 requests and excludes warm-up records. Each
category is grouped by benchmark, runner mode, and handler-reported cold status:

```text
<benchmark> <mode> <reported status>
110.dynamic-html burst warm
110.dynamic-html cold cold
110.dynamic-html warm warm
```

These group names remain in `summary.csv`. In all three plots, the benchmark
name appears in the title, and categories (or ECDF legend entries) instead show
explicit labels such as `Mode: warm` and `Observed: warm`. If several benchmarks
are analyzed together, category labels also include the benchmark name so they
remain distinguishable.

`Mode` is the runner's experiment mode; `Observed` is the handler's `is_cold`
value. They are different signals and need not agree in every call. Each plot
includes a key explaining:

- **Mode warm:** sequential requests after a warm-up.
- **Mode cold:** redeploy before each request.
- **Mode burst:** concurrent batches of requests.
- **Observed cold:** first request handled by the process.
- **Observed warm:** a subsequent request handled by that process.

Generated files:

- **`latency_box.png` — distribution summary.** One box per group, using
  `client_latency_s` converted to milliseconds and a logarithmic y-axis. It
  shows the median and quartiles; the default whiskers extend to the most
  extreme data points within 1.5 times the interquartile range, and points
  beyond them are shown as outliers. It does not break the latency into
  components.
- **`latency_ecdf.png` — cumulative distribution.** The x-axis is client
  latency in milliseconds (log scale); the y-axis is the fraction of successful
  requests with latency at or below that x-value. For example, `0.95` means
  95% of requests completed at or below that latency. Horizontal sections
  indicate no additional observed requests across that latency interval;
  vertical jumps indicate observed request latencies, with larger jumps where
  more requests have the same measured latency.
- **`time_breakdown.png` — mean latency components.** A stacked bar per group.
  For benchmarks without detailed timing fields, the segments are mean
  `function_time_s` and mean estimated `overhead_s`. When benchmark records
  contain `measurement_*_time` fields, those measured subcomponents are shown
  separately, with remaining function time and estimated overhead. The
  components are averaged per group; their means add to the corresponding mean
  client latency, subject to the benchmark's measurement fields being valid.
- **`summary.csv` — numeric statistics.** The analyzer writes count, mean,
  standard deviation, min, median, p95, p99, and max for client latency,
  function time, and estimated overhead (in seconds).

The box plot and ECDF show the **distribution of individual client
latencies**. The breakdown is also based on that end-to-end client latency, but
shows its mean split into the handler/function interval and estimated
outside-handler remainder (or finer benchmark-provided timing components).
Use the distribution plots to see variability/tails; use the breakdown to
compare average time composition.

## Repository map

- [`sebs-on-openfaas/`](./sebs-on-openfaas/) — runner, input generator,
  analyzer, OpenFaaS stack, and benchmark function folders.
- [`serverless-benchmarks/`](./serverless-benchmarks/) — SeBS source, benchmark
  input definitions, optional benchmark datasets, and storage CLI.
- [`sebs-on-openfaas-guide.md`](./sebs-on-openfaas-guide.md) — expanded
  walkthrough, benchmark-specific notes, and troubleshooting.