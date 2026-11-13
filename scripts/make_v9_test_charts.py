"""Generate the three v9 test charts from recorded evidence.

Usage: python scripts/make_v9_test_charts.py (requires matplotlib).
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
OUTPUT = ROOT / "docs" / "dissemination" / "figures" / "v9"
MODELS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "phi-4",
    "gpt-5.5",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "grok-4.7",
]
BLUE = "#0078D4"
ORANGE = "#B35900"
GREY = "#64748B"


def read_evidence(relative_path: str) -> dict:
    return json.loads((EVIDENCE / relative_path).read_text(encoding="utf-8-sig"))


def style(ax) -> None:
    ax.grid(axis="x", color="#E5E7EB")
    ax.set_axisbelow(True)
    for edge in ("top", "right", "left"):
        ax.spines[edge].set_visible(False)
    ax.tick_params(axis="y", length=0)


def save(fig, filename: str, note: str) -> None:
    fig.text(0.02, 0.025, note, fontsize=10, va="bottom", linespacing=1.5)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / filename
    fig.savefig(target, dpi=160, facecolor="white")
    plt.close(fig)
    print(f"Wrote {target}")


def score_chart() -> None:
    cells = read_evidence(
        "scenario-1-locking-the-score/combined-150/combined_stats.json"
    )["cells"]
    fig, ax = plt.subplots(figsize=(11, 7))
    for index, (arm, label, colour) in enumerate(
        [("A", "Model calculates from raw data", ORANGE),
         ("B", "Verified answer / receipt fallback", BLUE)]
    ):
        for row, model in enumerate(MODELS):
            y = row + (index - 0.5) * 0.32
            if model == "phi-4" and arm == "B":
                ax.text(0.6, y, "not tested", va="center", fontsize=10, color=GREY)
                continue
            metric = cells[f"{model}|{arm}"]["score_error_mae"]
            mean, low, high = (metric[key] for key in ("mean", "lo", "hi"))
            ax.barh(y, mean, height=0.28, color=colour,
                    label=label if row == 0 else None)
            if high > low:
                ax.errorbar(mean, y, xerr=[[mean - low], [high - mean]],
                            color="#1F2937", capsize=3)
            if mean == 0:
                ax.plot(0, y, "o", color=colour, markersize=4, clip_on=False)
            ax.text(high + 0.6, y, f"{mean:.1f}", va="center", fontsize=10)
    ax.set_yticks(range(len(MODELS)), MODELS)
    ax.invert_yaxis()
    ax.set_xlim(0, 47)
    ax.set_xlabel("Mean absolute score error (points); lower is better")
    ax.set_title("Test 1: keeping the score aligned with policy",
                 loc="left", fontsize=16, fontweight="bold", pad=45)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False, fontsize=10)
    style(ax)
    fig.subplots_adjust(left=0.19, right=0.97, top=0.80, bottom=0.21)
    save(fig, "test-1-score-consistency.png",
         "150 synthetic parcels; 3 repeats. Error bars: 95% confidence intervals.\n"
         "Zero delivered error is enforced by verification and receipt fallback, not model perfection.\n"
         "Phi-4: no tool-enabled arm. This compares approaches, not a model ranking.")


def fabric_iq_chart() -> None:
    chain = read_evidence("scenario-2-ontology/foundry_ontology_chain.json")["summary"]
    arms = read_evidence("scenario-2-ontology/chain_arms.json")["summary"]
    usefulness = read_evidence(
        "scenario-2-ontology/foundry_ontology_usefulness.json"
    )["summary"]
    rows = [("Fabric IQ MCP", chain["correct"] / chain["questions_run"],
             chain["questions_run"], BLUE)]
    for label, key, colour in [
        ("Python-backed tools", "foundry-graph", BLUE),
        ("Raw tables", "foundry-flat", ORANGE),
        ("No data", "bare-model", GREY),
    ]:
        rows.append((label, arms[key]["correct"], arms[key]["n"], colour))

    fig, (accuracy, advice) = plt.subplots(
        1, 2, figsize=(12, 6), gridspec_kw={"width_ratios": [1.4, 1]}
    )
    for row, (label, correct, count, colour) in enumerate(rows):
        accuracy.barh(row, correct * 100, height=0.55, color=colour)
        accuracy.text(correct * 100 + 2, row,
                      f"{correct * count:.0f}/{count} ({correct:.0%})",
                      va="center", fontsize=10)
    accuracy.set_yticks(range(len(rows)), [row[0] for row in rows])
    accuracy.invert_yaxis()
    accuracy.set_xlim(0, 145)
    accuracy.set_xticks([0, 25, 50, 75, 100])
    accuracy.set_xlabel("Correct answers (%)")
    accuracy.set_title("Connection questions", loc="left", fontsize=13)
    style(accuracy)

    mean = usefulness["mean_total_diff_D_minus_T"]
    low, high = usefulness["mean_total_diff_ci95_bootstrap_over_questions"]
    advice.axvline(0, color=GREY, linestyle="--")
    advice.errorbar(mean, 0, xerr=[[mean - low], [high - mean]],
                    fmt="o", color=BLUE, capsize=6, markersize=8)
    advice.text(0.5, 0.84, f"Mean gain: {mean:+.2f} / 15\n"
                f"95% CI: {low:.2f} to {high:.2f}",
                transform=advice.transAxes, ha="center", fontsize=11)
    advice.text(0.5, 0.16, "Inconclusive: interval includes zero",
                transform=advice.transAxes, ha="center", fontsize=10)
    advice.set_xlim(-2, 3)
    advice.set_ylim(-1, 1)
    advice.set_yticks([])
    advice.set_xlabel("Usefulness: agent minus template")
    advice.set_title("20 questions; 60 AI judgements", loc="left", fontsize=13)
    style(advice)
    fig.suptitle("Test 2: Fabric IQ answers and their usefulness",
                 fontsize=16, fontweight="bold", x=0.02, ha="left")
    fig.subplots_adjust(left=0.18, right=0.97, top=0.83, bottom=0.27, wspace=0.38)
    save(fig, "test-2-fabric-iq.png",
         "gpt-4.1 agents; usefulness judge: gpt-4.1-mini. All data synthetic.\n"
         "Five connection questions: MCP has 9 recorded runs; comparison arms have 3 repeats (15 runs).\n"
         "The Python-backed comparison did not use Fabric IQ.")


def scenario_tools_chart() -> None:
    models = [model for model in MODELS if model not in ("gpt-4.1-mini", "phi-4")]
    summaries = {}
    for model in models:
        filename = "foundry_arms.json" if model == "gpt-4.1" else f"foundry_arms_{model}.json"
        evidence = read_evidence(f"scenario-3-four-tools/{filename}")
        summaries[model] = evidence["summary"]
        for arm in ("foundry-graph", "foundry-flat", "bare-model"):
            if summaries[model][arm]["n"] != 20:
                raise ValueError(f"Expected 20 questions for {model}/{arm}")

    fig, ax = plt.subplots(figsize=(11, 7))
    for index, (arm, label, colour) in enumerate([
        ("foundry-graph", "Scenario tools", BLUE),
        ("foundry-flat", "Raw-table lookup tools", ORANGE),
        ("bare-model", "No data or tools", GREY),
    ]):
        ys = [row + (index - 1) * 0.24 for row in range(len(models))]
        values = [summaries[model][arm]["correct"] * 100 for model in models]
        ax.barh(ys, values, height=0.21, color=colour, label=label)
        for y, value in zip(ys, values):
            ax.text(value + 1, y, f"{value:.0f}%", va="center", fontsize=10)
    ax.set_yticks(range(len(models)), [
        f"{model}\n({'Fabric' if model == 'gpt-4.1' else 'local copy'})" for model in models
    ])
    ax.invert_yaxis()
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Correct answers (%); higher is better")
    ax.set_title("Test 3: Foundry Agent Tools", loc="left",
                 fontsize=16, fontweight="bold", pad=45)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False, fontsize=10, ncol=3)
    style(ax)
    fig.subplots_adjust(left=0.20, right=0.97, top=0.80, bottom=0.22)
    save(fig, "test-3-foundry-agent-tools.png",
         "20 questions per arm/model; 1 repeat; synthetic data. One answer changes the result by 5 points.\n"
         "Data copies gave the same expected answers, but data sources and tool-call limits differed.\n"
         "gpt-4.1 raw-table arm: 2 API errors. Raw-table arms had no SQL or code interpreter.")


def main() -> None:
    plt.rcParams.update({"font.size": 11, "axes.labelsize": 11})
    score_chart()
    fabric_iq_chart()
    scenario_tools_chart()


if __name__ == "__main__":
    main()
