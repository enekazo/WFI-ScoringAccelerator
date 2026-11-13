"""Regenerate article Figures 7 and 8 from the pooled 150-parcel results.

Usage: python scripts/make_fig07_150.py   (requires matplotlib)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "docs" / "evidence" / "scenario-1-locking-the-score" / "combined-150" / "combined_stats.json"
OUTS = [ROOT / "docs" / "dissemination" / "figures" / "v6", ROOT / "docs" / "dissemination" / "figures"]

MODELS = [
    ("gpt-4.1", "#0078d4"),
    ("gpt-4.1-mini", "#83b4e6"),
    ("phi-4", "#b35900"),
    ("gpt-5.5", "#c0392b"),
    ("gpt-6.1-terra", "#2e8b57"),
    ("gpt-6.1-sol", "#7fc8a0"),
    ("grok-4.7", "#6b4fa0"),
]
ARMS = [("A", "Arm A\nraw data"), ("A+", "Arm A+\nreceipt as a hint"),
        ("B(raw)", "Arm B (raw)\nbefore the verifier"), ("B", "Arm B\ndelivered")]
GREY = "#1f2937"


def save(fig, name: str) -> None:
    for d in OUTS:
        d.mkdir(parents=True, exist_ok=True)
        fig.savefig(d / name, dpi=150)
        print("Wrote", d / name)


def style(ax) -> None:
    ax.grid(axis="y", color="#e5e7eb")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def main() -> None:
    cells = json.loads(STATS.read_text())["cells"]

    fig, ax = plt.subplots(figsize=(13, 6.4))
    width = 0.115
    n = len(MODELS)
    for i, (m, color) in enumerate(MODELS):
        for j, (arm, _) in enumerate(ARMS):
            x = j + (i - (n - 1) / 2) * width
            c = cells.get(f"{m}|{arm}", {}).get("score_error_mae")
            if c is None or (m == "phi-4" and arm in ("B", "B(raw)")):
                ax.text(x, 1.0, "not run", rotation=90, ha="center", va="bottom",
                        fontsize=8, color="#6b7280", style="italic")
                continue
            ax.bar(x, c["mean"], width * 0.92, color=color, label=m if j == 0 else None)
            top = c["mean"]
            if arm in ("A", "A+") and c["hi"] > c["lo"]:
                ax.errorbar(x, c["mean"], yerr=[[c["mean"] - c["lo"]], [c["hi"] - c["mean"]]],
                            color=GREY, capsize=3, lw=1.2)
                top = c["hi"]
            ax.text(x, top + 0.8, f"{c['mean']:.1f}", ha="center", va="bottom", fontsize=7.5, color=GREY)
    ax.set_xticks(range(len(ARMS)), [lbl for _, lbl in ARMS], fontsize=10)
    ax.set_ylim(0, 60)
    ax.set_ylabel("Mean absolute error (points, 0-100)")
    style(ax)
    fig.text(0.01, 0.97, "Score error by arm and model", fontsize=17, fontweight="bold", color=GREY, va="top")
    fig.text(0.01, 0.915, "Mean absolute error in points; 150 synthetic parcels, 3 repeats. "
             "'not run' = arm not executed (not zero).", fontsize=10, color="#6b7280", va="top")
    ax.legend(ncol=7, fontsize=9, loc="upper center", frameon=False, bbox_to_anchor=(0.5, 1.02))
    fig.text(0.01, 0.01, "Error bars: 95% CI for Arms A and A+ where non-degenerate. "
             "Arm B delivered = 0 by construction (verifier replaces mismatched answers).\n"
             "Rows that failed on rate limits or timeouts were re-run; four Phi-4 rows still carry API errors. "
             "Phi-4: Arm B not run (endpoint rejects tool calls).", fontsize=8, color="#6b7280", va="bottom")
    fig.subplots_adjust(top=0.84, bottom=0.2, left=0.07, right=0.99)
    save(fig, "fig07_score_error.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    vals = [(m, cells[f"{m}|A"]["score_error_mae"], c) for m, c in MODELS]
    vals.sort(key=lambda v: -v[1]["mean"])
    for k, (m, c, color) in enumerate(vals):
        ax.barh(k, c["mean"], color=color)
        if c["hi"] > c["lo"]:
            ax.errorbar(c["mean"], k, xerr=[[c["mean"] - c["lo"]], [c["hi"] - c["mean"]]],
                        color=GREY, capsize=3, lw=1.2)
        ax.text(c["hi"] + 0.6, k, f"{c['mean']:.1f}", va="center", fontsize=9, color=GREY)
    ax.set_yticks(range(len(vals)), [v[0] for v in vals])
    ax.invert_yaxis()
    ax.set_xlabel("Mean absolute error (points, 0-100), Arm A (raw data)")
    ax.set_xlim(0, 48)
    style(ax)
    ax.grid(axis="x", color="#e5e7eb")
    ax.grid(axis="y", visible=False)
    ax.set_title("Arm A score error by model (150 parcels, 3 repeats)", loc="left", fontsize=13,
                 fontweight="bold", color=GREY)
    fig.tight_layout()
    save(fig, "fig08_model_comparison.png")


if __name__ == "__main__":
    main()
