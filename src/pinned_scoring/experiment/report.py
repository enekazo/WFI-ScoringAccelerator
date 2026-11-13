"""Markdown summary table."""

COLUMNS = [("score_error", "Score err (MAE)"), ("band_mismatch", "Band mismatch"),
           ("provenance_misstatement", "Provenance misstated"),
           ("fault_detection", "Fault detection"), ("false_alarm", "False alarm"),
           ("reproducibility", "Reproducible"), ("verifier_block", "Verifier-block rate"),
           ("parse_failure", "Parse fail")]


def _cell(ci: dict) -> str:
    if not ci or ci["mean"] is None:
        return "n/a"
    return f"{ci['mean']:.3f} [{ci['lo']:.3f}, {ci['hi']:.3f}]"


def table(metrics: dict) -> list:
    lines = ["| Model | Arm | " + " | ".join(h for _, h in COLUMNS) + " |",
             "|---|---|" + "---|" * len(COLUMNS)]
    for key, m in metrics.items():
        model, arm = key.split("|")
        lines.append(f"| {model} | {arm} | " + " | ".join(_cell(m.get(k)) for k, _ in COLUMNS) + " |")
    return lines


def render_summary(result: dict) -> str:
    out = ["# Experiment summary", "",
           f"Mode: {'DRY RUN (stub models, no network)' if result['dry_run'] else 'live'}  ",
           f"Models: {', '.join(result['models'])}  ",
           f"Parcels: {len(result['parcels'])}, repeats: {result['repeats']}", "",
           "Cells are mean [95% bootstrap CI over parcels]. Arm B shows the delivered answer "
           "(after the verifier) and B(raw), the model's answer before verification.", "",
           *table(result["metrics"])]
    sens = result.get("sensitivity")
    if sens:
        det = sens["deterministic"]
        out += ["", "## Weight sensitivity (+/-20%)", "",
                "Deterministic rescoring of clean parcels per variant:", "",
                "| Variant | Mean abs score shift | Band flip share |", "|---|---|---|"]
        out += [f"| {r['variant']} | {r['mean_abs_score_shift']:.3f} | {r['band_flip_share']:.3f} |"
                for r in det]
        out += ["", f"LLM arms pooled over {len(det)} variants, parcels "
                f"{', '.join(sens['llm']['parcels'])}, 1 repeat:", "",
                *table(sens["llm"]["metrics"])]
    return "\n".join(out) + "\n"
