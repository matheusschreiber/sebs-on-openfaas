#!/usr/bin/env python3
"""Summarise results.jsonl and draw plots (latency box plot, ECDF, time breakdown)."""
import argparse, json, os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("results", nargs="+", help="one or more results.jsonl files")
ap.add_argument("--outdir", default="plots")
a = ap.parse_args()
os.makedirs(a.outdir, exist_ok=True)

rows = [json.loads(l) for p in a.results for l in open(p) if l.strip()]
df = pd.json_normalize(rows, sep="_")
ok = df[(df.http_status == 200) & (~df.warmup)].copy()
ok["group"] = ok["benchmark"] + " " + ok["mode"] + " " + ok["is_cold"].map({True: "cold", False: "warm"})
ok["overhead_s"] = ok["client_latency_s"] - ok["function_time_s"]   # gateway + network + wrapper

# 1. table: median / p95 / p99 (seconds)
summary = ok.groupby("group")[["client_latency_s", "function_time_s", "overhead_s"]].describe(
    percentiles=[.5, .95, .99]).round(4)
summary.to_csv(os.path.join(a.outdir, "summary.csv"))
print(summary[[("client_latency_s", c) for c in ("count", "mean", "50%", "95%", "99%")]])
print("failed calls:", int(((df.http_status != 200) & (~df.warmup)).sum()), "of", int((~df.warmup).sum()))

groups = sorted(ok["group"].unique())
benchmarks = sorted(ok["benchmark"].unique())
benchmark_title = ", ".join(benchmarks)
labels = {}
for _, row in ok.drop_duplicates("group").iterrows():
    status = "cold" if row["is_cold"] else "warm"
    label = "Mode: %s\nObserved: %s" % (row["mode"], status)
    if len(benchmarks) > 1:
        label = row["benchmark"] + "\n" + label
    labels[row["group"]] = label


def describe_plot(fig, ax, title):
    ax.set_title(title + "\n" + benchmark_title)
    fig.text(
        0.5, 0.02,
        "Mode: warm = sequential after warm-up; cold = redeploy before each call;\n"
        "burst = concurrent batches. Observed: cold = first request in the process;\n"
        "warm = a subsequent request (reported by the handler).",
        ha="center", va="bottom", fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.16, 1, 1))


# 2. box plot of client latency
fig, ax = plt.subplots(figsize=(1.6 * len(groups) + 3, 4))
ax.boxplot([ok[ok.group == g]["client_latency_s"] * 1000 for g in groups])
ax.set_xticks(range(1, len(groups) + 1)); ax.set_xticklabels([labels[g] for g in groups])
ax.set_ylabel("client latency [ms]"); ax.set_yscale("log")
describe_plot(fig, ax, "Client latency distribution")
fig.savefig(os.path.join(a.outdir, "latency_box.png"), dpi=150); plt.close(fig)

# 3. ECDF
fig, ax = plt.subplots(figsize=(6, 4))
for g in groups:
    v = ok[ok.group == g]["client_latency_s"].sort_values() * 1000
    ax.step(v, [(i + 1) / len(v) for i in range(len(v))], where="post", label=labels[g])
ax.set_xlabel("client latency [ms]"); ax.set_ylabel("fraction of requests"); ax.set_xscale("log"); ax.legend(fontsize=7)
describe_plot(fig, ax, "Client latency ECDF")
fig.savefig(os.path.join(a.outdir, "latency_ecdf.png"), dpi=150); plt.close(fig)

# 4. where does the time go? (mean per group)
parts = {"function (total)": "function_time_s", "gateway+network": "overhead_s"}
meas = [c for c in ok.columns if c.startswith("measurement_") and c.endswith("_time")]
if meas:   # benchmarks with storage report download/compute/upload split (microseconds)
    for c in meas:
        ok[c + "_s"] = ok[c] / 1e6
    parts = {c.replace("measurement_", ""): c + "_s" for c in meas}
    ok["other_function_s"] = ok["function_time_s"] - sum(ok[c + "_s"] for c in meas)
    parts["other (function)"] = "other_function_s"
    parts["gateway+network"] = "overhead_s"
means = ok.groupby("group")[list(parts.values())].mean() * 1000
means.columns = list(parts.keys())
ax = means.plot(kind="bar", stacked=True, figsize=(1.6 * len(groups) + 3, 4))
ax.set_xticklabels([labels[g] for g in means.index], rotation=0)
ax.set_xlabel("")
ax.set_ylabel("mean time [ms]")
describe_plot(ax.figure, ax, "Mean client latency breakdown")
ax.figure.savefig(os.path.join(a.outdir, "time_breakdown.png"), dpi=150); plt.close(ax.figure)
print("plots written to", a.outdir)