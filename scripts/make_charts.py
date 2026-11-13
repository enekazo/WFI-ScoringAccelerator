"""Generate README charts from committed evidence (no LLM calls).

Usage: python scripts/make_charts.py   (requires matplotlib: pip install -e .[charts])
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "docs" / "evidence"
OUT = EV / "scenario-1-locking-the-score" / "charts"
S1 = EV / "scenario-1-locking-the-score"
S2 = EV / "scenario-2-ontology"
S3 = EV / "scenario-3-four-tools"

MODELS = ["gpt-4.1", "gpt-4.1-mini"]
ARMS = ["A", "A+", "B(raw)", "B"]
ARM_LABELS = {
    "A": "A: raw LLM",
    "A+": "A+: raw LLM + hints",
    "B(raw)": "B(raw): agent, unverified",
    "B": "B: pinned + verifier",
}
COLORS = ["#c0392b", "#e67e22", "#f1c40f", "#27ae60"]


def _save(fig, name: str) -> None:
    out = OUT
    if name.startswith("usefulness"):
        out = S2 / "charts"
    elif name.startswith("scenarios"):
        out = S3 / "charts"
    out.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out / name, dpi=150)
    plt.close(fig)


def _grouped(metrics: dict, key: str, title: str, ylabel: str, name: str, scale: float = 1.0) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    width = 0.2
    for i, arm in enumerate(ARMS):
        vals, errs = [], []
        for m in MODELS:
            c = metrics.get(f"{m}|{arm}", {}).get(key)
            if c is None:
                vals.append(0)
                errs.append([0, 0])
                continue
            vals.append(c["mean"] * scale)
            errs.append([(c["mean"] - c["lo"]) * scale, (c["hi"] - c["mean"]) * scale])
        xs = [j + (i - 1.5) * width for j in range(len(MODELS))]
        ax.bar(xs, vals, width, label=ARM_LABELS[arm], color=COLORS[i],
               yerr=list(zip(*errs)), capsize=3)
    ax.set_xticks(range(len(MODELS)), [m.replace("-cmp", "") for m in MODELS])
    if any(f"{m}|B" not in metrics for m in MODELS):
        ax.annotate("Arms B and B(raw) not run for phi-4 (no tool calling)",
                    xy=(0.01, 0.01), xycoords="axes fraction", fontsize=7, color="gray")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(fontsize=8, loc="upper center", ncol=2)
    ax.set_ylim(0, ax.get_ylim()[1] * 1.2)
    _save(fig, name)


def _scenarios_chart() -> None:
    path = S3 / "scenarios.json"
    if not path.exists():
        return
    summ = json.loads(path.read_text())["summary"]
    keys = [("correct", "Correct"), ("provenance_ok", "Provenance"), ("numbers_pinned", "Numbers pinned")]
    fig, axes = plt.subplots(1, len(summ), figsize=(5.5 * len(summ), 4.5), squeeze=False)
    for ax, (model, s) in zip(axes[0], summ.items()):
        for i, (arm, color) in enumerate((("graph", "#27ae60"), ("flat", "#c0392b"))):
            vals, errs = [], []
            for k, _ in keys:
                c = s[arm]["overall"][k]
                vals.append(c["mean"] * 100)
                errs.append([(c["mean"] - c["lo"]) * 100, (c["hi"] - c["mean"]) * 100])
            xs = [j + (i - 0.5) * 0.35 for j in range(len(keys))]
            ax.bar(xs, vals, 0.35, label=f"{arm} agent", color=color, yerr=list(zip(*errs)), capsize=3)
        ax.set_xticks(range(len(keys)), [lbl for _, lbl in keys])
        ax.set_ylim(0, 115)
        ax.set_ylabel("% of questions (95% CI)")
        ax.set_title(f"{model}: graph vs flat-table (synthetic)")
        ax.legend(fontsize=8, loc="upper right")
    _save(fig, "scenarios_graph_vs_flat.png")


def main() -> None:
    metrics = json.loads((S1 / "metrics.json").read_text())["metrics"]
    _grouped(metrics, "score_error", "Score error vs pinned score (lower is better; 95% CI)",
             "Mean absolute error (points, 0-100)", "score_error_mae.png")
    _grouped(metrics, "provenance_misstatement",
             "Answers that misstate data provenance (lower is better; 95% CI)",
             "Share of answers (%)", "provenance_misstatement.png", scale=100)

    blocks = json.loads((S1 / "block-analysis-after.json").read_text())
    groups = {
        "Genuine wrong content": ["wrong_structured"],
        "HTTP 429 / infra error": ["infra_error"],
        "Wording false block": ["reason_wording", "provenance_wording"],
    }
    gcolors = ["#27ae60", "#7f8c8d", "#e67e22"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bottoms = [0] * len(MODELS)
    for (label, cats), col in zip(groups.items(), gcolors):
        vals = [sum(blocks[m]["categories"].get(c, 0) for c in cats) for m in MODELS]
        ax.bar(MODELS, vals, bottom=bottoms, label=label, color=col)
        for j, v in enumerate(vals):
            if v:
                ax.text(j, bottoms[j] + v / 2, str(v), ha="center", va="center", color="white")
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_ylabel("Blocked answers (of 150 runs per model)")
    ax.set_title("Why the verifier blocked answers (after wording fix)")
    ax.legend(fontsize=8)
    _save(fig, "verifier_block_causes.png")

    u = json.loads((S2 / "usefulness.json").read_text())["summary"]["overall"]
    labels = ["win", "tie", "loss"]
    shares = [u[k]["share"] * 100 for k in labels]
    err = [[u[k]["share"] * 100 - u[k]["ci95"][0] * 100 for k in labels],
           [u[k]["ci95"][1] * 100 - u[k]["share"] * 100 for k in labels]]
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.bar(labels, shares, yerr=err, capsize=5, color=["#27ae60", "#95a5a6", "#c0392b"])
    for i, k in enumerate(labels):
        ax.text(i, 2, f"{u[k]['count']}/{u['n']}", ha="center", color="white")
    ax.set_ylabel("Share of blind judgements (%)")
    ax.set_title("Agent answer vs plain template (95% Wilson CI)")
    _save(fig, "usefulness_win_tie_loss.png")
    _scenarios_chart()
    print(f"Charts written to {OUT}")


def model_comparison() -> None:
    """Score-error / provenance charts across gpt-4.1 (baseline) and newer-model runs."""
    global MODELS, OUT
    metrics = dict(json.loads((S1 / "metrics.json").read_text())["metrics"])
    MODELS = ["gpt-4.1"]
    for f in sorted((S1 / "model-comparison").glob("*/metrics.json")):
        new = json.loads(f.read_text())["metrics"]
        metrics.update(new)
        MODELS += sorted({k.split("|")[0] for k in new})
    order = ["gpt-4.1", "phi-4-cmp", "gpt-6.1-terra", "gpt-6.1-sol", "grok-4.7-cmp"]
    MODELS = sorted(MODELS, key=lambda m: order.index(m) if m in order else 99)
    OUT = S1 / "charts" / "model-comparison"
    _grouped(metrics, "score_error", "Score error vs pinned score by model (lower is better; 95% CI)",
             "Mean absolute error (points, 0-100)", "score_error_mae.png")
    _grouped(metrics, "provenance_misstatement",
             "Answers that misstate data provenance by model (lower is better; 95% CI)",
             "Share of answers (%)", "provenance_misstatement.png", scale=100)
    print(f"Charts written to {OUT}")


if __name__ == "__main__":
    import sys
    model_comparison() if "--model-comparison" in sys.argv else main()
