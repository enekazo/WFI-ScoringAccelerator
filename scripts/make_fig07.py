"""Regenerate the article figure 'Score error by arm and model' (six models) from committed evidence.

Usage: python scripts/make_fig07.py   (requires matplotlib)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "docs" / "evidence" / "scenario-1-locking-the-score"
OUT = ROOT / "docs" / "dissemination" / "figures" / "fig07_score_error.png"

# (display name, metrics key prefix, colour)
MODELS = [
    ("gpt-4.1", "gpt-4.1", "#0078d4"),
    ("gpt-4.1-mini", "gpt-4.1-mini", "#83b4e6"),
    ("Phi-4", "phi-4-cmp", "#b35900"),
    ("gpt-6.1-terra", "gpt-6.1-terra", "#2e8b57"),
    ("gpt-6.1-sol", "gpt-6.1-sol", "#7fc8a0"),
    ("grok-4.7", "grok-4.7-cmp", "#6b4fa0"),
]
ARMS = [
    ("A", "Arm A\nraw data"),
    ("A+", "Arm A+\nreceipt as a hint"),
    ("B(raw)", "Arm B (raw)\nbefore the verifier"),
    ("B", "Arm B\ndelivered"),
]


def load() -> dict:
    metrics = dict(json.loads((EV / "metrics.json").read_text())["metrics"])
    for f in sorted((EV / "model-comparison").glob("*/metrics.json"), key=lambda p: p.parent.name):
        metrics.update(json.loads(f.read_text())["metrics"])
    return metrics


def main() -> None:
    metrics = load()
    fig, ax = plt.subplots(figsize=(13, 6.4))
    width = 0.13
    n = len(MODELS)
    for i, (label, key, color) in enumerate(MODELS):
        for j, (arm, _) in enumerate(ARMS):
            x = j + (i - (n - 1) / 2) * width
            c = metrics.get(f"{key}|{arm}", {}).get("score_error")
            if c is None:
                ax.text(x, 1.2, "not run", rotation=90, ha="center", va="bottom",
                        fontsize=8, color="#6b7280", style="italic")
                continue
            ax.bar(x, c["mean"], width * 0.92, color=color, label=label if j == 0 else None)
            top = c["mean"]
            if arm == "A" and c["hi"] > c["lo"]:
                ax.errorbar(x, c["mean"], yerr=[[c["mean"] - c["lo"]], [c["hi"] - c["mean"]]],
                            color="#1f2937", capsize=3, lw=1.2)
                top = c["hi"]
            ax.text(x, top + 1.0, f"{c['mean']:.1f}", ha="center", va="bottom", fontsize=7.5,
                    color="#1f2937")
    ax.set_xticks(range(len(ARMS)), [lbl for _, lbl in ARMS], fontsize=10)
    ax.set_ylim(0, 75)
    ax.set_ylabel("Mean absolute error (points, 0-100)")
    ax.grid(axis="y", color="#e5e7eb")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.text(0.01, 0.97, "Score error by arm and model", fontsize=17, fontweight="bold",
             color="#1f2937", va="top")
    fig.text(0.01, 0.915, "Mean absolute error in points; 50 synthetic parcels, 3 repeats. "
             "'not run' = arm not executed (not zero).", fontsize=10, color="#6b7280", va="top")
    ax.legend(ncol=6, fontsize=9, loc="upper center", frameon=False, bbox_to_anchor=(0.5, 1.02))
    fig.text(0.01, 0.01,
             "Error bars: 95% CI for Arm A where non-degenerate (gpt-4.1 34.0-63.5; Phi-4 6.1-13.8). "
             "Arm B delivered = 0 by construction (verifier replaces mismatched answers).\n"
             "Phi-4: Arm B not run (endpoint rejects tool calls, HTTP 400). gpt-4.1 and Phi-4 ran at "
             "temperature 0; gpt-6.1 models reject the temperature setting (default reasoning effort).",
             fontsize=8, color="#6b7280", va="bottom")
    fig.subplots_adjust(top=0.84, bottom=0.2, left=0.07, right=0.99)
    fig.savefig(OUT, dpi=150)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
