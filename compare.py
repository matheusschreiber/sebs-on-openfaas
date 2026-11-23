#!/usr/bin/env python3
"""Compare several benchmark runs (e.g. CPython vs PyPy) and draw comparison plots."""
import argparse, json, os

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--run", nargs=2, action="append", metavar=("LABEL", "RESULTS"), required=True,
                help="label and results.jsonl of one run; repeat the option for each run to compare")
ap.add_argument("--outdir", default="input-output/comparison")
a = ap.parse_args()
os.makedirs(a.outdir, exist_ok=True)

frames = []
for label, path in a.run:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    frame = pd.json_normalize(rows, sep="_")
    frame["variant"] = label
    frames.append(frame)
df = pd.concat(frames, ignore_index=True)
ok = df[(df.http_status == 200) & (~df.warmup)].copy()
ok["overhead_s"] = ok["client_latency_s"] - ok["function_time_s"]   # gateway + network + wrapper

variants = [label for label, _ in a.run]
modes = [m for m in ("warm", "cold", "burst") if m in set(ok["mode"])]
benchmark_title = ", ".join(sorted(ok["benchmark"].unique()))
colors = plt.cm.tab10.colors

# 1. table: median / p95 / p99 per variant and mode (seconds)
summary = ok.groupby(["mode", "variant"])[["client_latency_s", "function_time_s", "overhead_s"]].describe(
    percentiles=[.5, .95, .99]).round(4)
summary.to_csv(os.path.join(a.outdir, "summary.csv"))
print(summary[[("client_latency_s", c) for c in ("count", "mean", "50%", "95%", "99%")]])


def describe_plot(fig, ax, title):
    ax.set_title(title + "\n" + benchmark_title)
    fig.text(
        0.5, 0.02,
        "Mode: warm = sequential after warm-up; cold = redeploy before each call;\n"
        "burst = concurrent batches.",
        ha="center", va="bottom", fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.12, 1, 1))


def grouped_box(column, ylabel, title, fname):
    """Box plot of `column` with one group per mode and one box per variant."""
    fig, ax = plt.subplots(figsize=(2.2 * len(modes) + 3, 4))
    width = 0.8 / len(variants)
    for vi, variant in enumerate(variants):
        values = [ok[(ok["mode"] == m) & (ok.variant == variant)][column].dropna() * 1000 for m in modes]
        positions = [mi + (vi - (len(variants) - 1) / 2) * width for mi in range(len(modes))]
        bp = ax.boxplot(values, positions=positions, widths=width * 0.85, patch_artist=True)
        for box in bp["boxes"]:
            box.set_facecolor(colors[vi % len(colors)]); box.set_alpha(0.6)
    ax.set_xticks(range(len(modes))); ax.set_xticklabels(["Mode: %s" % m for m in modes])
    ax.set_ylabel(ylabel); ax.set_yscale("log")
    ax.legend(handles=[mpatches.Patch(color=colors[vi % len(colors)], alpha=0.6, label=v)
                       for vi, v in enumerate(variants)], fontsize=8)
    describe_plot(fig, ax, title)
    fig.savefig(os.path.join(a.outdir, fname), dpi=150); plt.close(fig)


# 2. box plots: client latency and function time, variants side by side per mode
grouped_box("client_latency_s", "client latency [ms]", "Client latency comparison", "latency_box.png")
grouped_box("function_time_s", "function time [ms]", "Function time comparison", "function_time_box.png")

# 3. ECDF of client latency: one panel per mode, one line per variant
fig, axes = plt.subplots(1, len(modes), figsize=(4 * len(modes), 4), sharey=True)
axes = [axes] if len(modes) == 1 else list(axes)
for ax, mode in zip(axes, modes):
    for vi, variant in enumerate(variants):
        v = ok[(ok["mode"] == mode) & (ok.variant == variant)]["client_latency_s"].sort_values() * 1000
        if len(v):
            ax.step(v, [(i + 1) / len(v) for i in range(len(v))], where="post",
                    label=variant, color=colors[vi % len(colors)])
    ax.set_title("Mode: %s" % mode, fontsize=9)
    ax.set_xlabel("client latency [ms]"); ax.set_xscale("log"); ax.legend(fontsize=7)
    ax.xaxis.set_major_locator(mticker.LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: "%g" % x))
    ax.xaxis.set_minor_formatter(mticker.NullFormatter())
axes[0].set_ylabel("fraction of requests")
fig.suptitle("Client latency ECDF comparison\n" + benchmark_title)
fig.text(
    0.5, 0.02,
    "Mode: warm = sequential after warm-up; cold = redeploy before each call;\n"
    "burst = concurrent batches.",
    ha="center", va="bottom", fontsize=8,
)
fig.tight_layout(rect=(0, 0.12, 1, 0.9))
fig.savefig(os.path.join(a.outdir, "latency_ecdf.png"), dpi=150); plt.close(fig)

# 4. Mean time breakdown (function vs gateway+network)
means = (ok.groupby(["mode", "variant"])[["function_time_s", "overhead_s"]]
         .mean().mul(1000)
         .reindex(pd.MultiIndex.from_product([modes, variants])))

bar_width, compiler_gap, mode_gap = 0.65, 0.20, 1.8
function_color, overhead_color = "#4C78A8", "#F2A541"

fig, ax = plt.subplots(figsize=(max(9, len(modes) * 4.5), 5))
x, centers = [], []
pos = 0

for mode in modes:
    start = pos
    for variant in variants:
        function, overhead = means.loc[(mode, variant)]
        x.append(pos)
        ax.bar(pos, function, bar_width, color=function_color, edgecolor="black", linewidth=.6)
        ax.bar(pos, overhead, bar_width, bottom=function, color=overhead_color,
               edgecolor="black", linewidth=.6)
        pos += bar_width + compiler_gap
    centers.append((start + pos - bar_width - compiler_gap) / 2)
    pos += mode_gap

ax.set_xticks(x)
label_map = {
    "CPython": "CPython",
    "PyPy (JIT)": "JIT",
    "PyPy (no JIT)": "no-JIT",
}

ax.set_xticklabels(
    [label_map[v] for v in variants] * len(modes),
    fontsize=9
)

for i, (mode, center) in enumerate(zip(modes, centers)):
    ax.text(center, 1.02, mode.upper(), transform=ax.get_xaxis_transform(),
            ha="center", fontweight="bold", fontsize=11)
    if i < len(modes) - 1:
        ax.axvline((x[(i+1)*len(variants)-1] + x[(i+1)*len(variants)]) / 2,
                   color="gray", linestyle="--", alpha=.5)

ax.set_ylabel("Mean time [ms]")
ax.set_xlabel("Compiler")
ax.set_title("Mean client latency breakdown comparison\n" + benchmark_title, pad=30)
ax.grid(axis="y", linestyle=":", alpha=.4)
ax.set_axisbelow(True)
ax.legend(handles=[
    mpatches.Patch(color=function_color, label="Function execution"),
    mpatches.Patch(color=overhead_color, label="Gateway + network"),
], loc="center left", bbox_to_anchor=(1.02, .5), frameon=False, fontsize=9)

fig.text(.5, .02, "Warm = sequential after warm-up; cold = redeploy before each call; burst = concurrent batches.",
         ha="center", fontsize=8)
fig.tight_layout(rect=(0, .08, .88, .88))
fig.savefig(os.path.join(a.outdir, "time_breakdown.png"), dpi=150, bbox_inches="tight")
plt.close(fig)

print("comparison plots written to", a.outdir)
