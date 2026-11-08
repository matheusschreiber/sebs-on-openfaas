#!/usr/bin/env python3
"""Minimal harness: invoke an OpenFaaS function and write one JSON line per invocation.

Modes (loosely modelled on the SeBS perf-cost experiment):
  warm   1 discarded warm-up call, then N sequential calls
  cold   N times: redeploy the function (new container), wait until ready, then 1 call
  burst  R rounds of N concurrent calls
"""
import argparse, importlib.util, json, subprocess, time, uuid
from concurrent.futures import ThreadPoolExecutor

import requests


def invoke(url, payload, timeout):
    sent = time.time()
    status, error, body, raw = 0, None, {}, ""
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        status = r.status_code
        raw = r.text
        try:
            body = r.json()
        except ValueError:
            error = raw[:500]
        if not r.ok and not error:          # was: `error is None`, which missed empty bodies
            error = "HTTP %d" % status
    except requests.RequestException as e:
        error = str(e)[:500]
    recv = time.time()
    if not isinstance(body, dict):
        body = {}
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    return {
        "client_send": sent, "client_recv": recv, "client_latency_s": recv - sent,
        "http_status": status, "error": error,
        "function_begin": body.get("begin"), "function_end": body.get("end"),
        "function_time_s": body["results_time"] / 1e6 if "results_time" in body else None,
        "is_cold": body.get("is_cold"), "container_id": body.get("container_id"),
        "request_id": body.get("request_id"),
        "measurement": result.get("measurement", {}),
        "_output": result,            # removed before writing unless --keep-output
        "_raw": raw,                  # removed before writing
    }
    
def dump_logs(gateway, name, lines=100):
    """Print the function's recent container logs (where the Python traceback lives)."""
    print("\n===== faas-cli logs %s (last %d lines) =====" % (name, lines))
    try:
        p = subprocess.run(
            ["faas-cli", "logs", name, "--gateway", gateway, "--follow=false", "--lines", str(lines)],
            capture_output=True, text=True, timeout=30)
        print(p.stdout.strip() or "(no stdout)")
        if p.stderr.strip():
            print("--- stderr ---\n" + p.stderr.strip())
    except (subprocess.SubprocessError, OSError) as e:
        print("could not fetch logs: %s" % e)
    print("===== end logs =====\n")


def force_cold(stack, gateway, name):
    """Re-deploy with a changed env var so faasd replaces the container (SeBS does the same trick)."""
    subprocess.run(["faas-cli", "deploy", "-f", stack, "--gateway", gateway, "--filter", name,
                    "--env", "SEBS_COLD_TOKEN=" + uuid.uuid4().hex[:8]],
                   check=True)


def wait_ready(stack, gateway, name, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        out = subprocess.run(["faas-cli", "describe", name, "-f", stack, "--gateway", gateway],
                             capture_output=True, text=True).stdout
        if "Ready" in out:
            return
        time.sleep(0.5)
    raise RuntimeError("function did not become ready")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True, help="e.g. http://127.0.0.1:8080")
    ap.add_argument("--function", required=True, help="function name in the stack file")
    ap.add_argument("--stack", default="stack.yaml")
    ap.add_argument("--input", default="input.json")
    ap.add_argument("--benchmark", required=True, help="label stored in every record, e.g. 110.dynamic-html")
    ap.add_argument("--mode", choices=["warm", "cold", "burst"], required=True)
    ap.add_argument("-n", type=int, default=50, help="calls (warm/cold) or concurrency (burst)")
    ap.add_argument("--rounds", type=int, default=5, help="burst rounds")
    ap.add_argument("--settle", type=float, default=1.0, help="seconds to wait after 'ready' in cold mode (not counted in client latency)")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--out", default="results.jsonl")
    ap.add_argument("--keep-output", action="store_true", help="store the function output in the log")
    ap.add_argument("--input-module", help="path to the benchmark's input.py to validate the first output")
    ap.add_argument("--data-dir", default=None)
    a = ap.parse_args()

    payload = json.load(open(a.input))
    url = "%s/function/%s" % (a.gateway.rstrip("/"), a.function)
    run_id = uuid.uuid4().hex[:8]
    records = []

    def add(rec, **extra):
        rec.update(benchmark=a.benchmark, mode=a.mode, run_id=run_id, **extra)
        records.append(rec)

    if a.mode == "warm":
        add(invoke(url, payload, a.timeout), idx=-1, warmup=True)
        for i in range(a.n):
            add(invoke(url, payload, a.timeout), idx=i, warmup=False)
    elif a.mode == "cold":
        for i in range(a.n):
            t0 = time.time()
            force_cold(a.stack, a.gateway, a.function)
            wait_ready(a.stack, a.gateway, a.function)
            time.sleep(a.settle)
            rec = invoke(url, payload, a.timeout)
            rec["cold_total_s"] = rec["client_recv"] - t0       # deploy + ready + settle + first call
            add(rec, idx=i, warmup=False)
    else:  # burst
        for rnd in range(a.rounds):
            with ThreadPoolExecutor(max_workers=a.n) as ex:
                for i, rec in enumerate(ex.map(lambda _: invoke(url, payload, a.timeout), range(a.n))):
                    add(rec, idx=i, round=rnd, warmup=False)

    if a.input_module:   # validate the first successful output with the benchmark's own checker
        spec = importlib.util.spec_from_file_location("bench_input", a.input_module)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        first = next((r for r in records if r["http_status"] == 200), None)
        verdict = mod.validate_output(a.data_dir, payload, first["_output"], "python", None) if first else "no successful call"
        print("validate_output:", "OK" if verdict is None else verdict)
        if first is None:
            for i, record in enumerate(records, start=1):
                print("failed call %d: HTTP status %s; error: %s" % (
                    i, record["http_status"], record["error"] or "no error details"))
                if record["_raw"]:
                    print("raw response body: %s" % record["_raw"][:1000])
                if record["_output"]:
                    print("response body:")
                    print(json.dumps(record["_output"], indent=2, default=str))
            dump_logs(a.gateway, a.function)

    with open(a.out, "a") as f:
        for r in records:
            out = r.pop("_output")
            r.pop("_raw")
            if a.keep_output:
                r["output"] = out
            f.write(json.dumps(r, default=str) + "\n")

    ok = [r for r in records if r["http_status"] == 200]
    print("%d/%d calls OK, results appended to %s (run_id %s)" % (len(ok), len(records), a.out, run_id))
    if a.mode == "cold" and ok and not all(r["is_cold"] for r in ok):
        print("WARNING: some 'cold' calls were served by a warm container - redeploy did not replace it")


if __name__ == "__main__":
    main()