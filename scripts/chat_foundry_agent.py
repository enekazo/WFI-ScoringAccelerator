"""Chat with the Foundry pinned-score-agent; answers are verified against receipts before display."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pinned_scoring import foundry_agent as fa  # noqa: E402


def ask(openai_client, toolbox, question, show_tools):
    trace = []
    result = fa.chat(openai_client, toolbox, question, trace=trace)
    if show_tools:
        for t in trace:
            print(f"  [tool] {t['tool']}({t['arguments']})")
    if result.blocked:
        print("BLOCKED by verifier:")
        for v in result.violations:
            print(f"  - {v.kind}: {v.detail}")
    else:
        print(result.text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question", nargs="?", help="one-shot question; omit for interactive chat")
    ap.add_argument("--receipts", default=str(fa.DEFAULT_DIR), help="receipts directory")
    ap.add_argument("--endpoint")
    ap.add_argument("--agent", default=fa.AGENT_NAME)
    ap.add_argument("--show-tools", action="store_true")
    ap.add_argument("--trace", action="store_true",
                    help="print OpenTelemetry spans for agent runs to the console (optional)")
    a = ap.parse_args(argv)
    if a.trace and not fa.enable_tracing(console=True):
        print("tracing unavailable: pip install opentelemetry-sdk", file=sys.stderr)

    toolbox = fa.FoundryToolbox(a.receipts)
    openai_client = fa.make_project_client(a.endpoint).get_openai_client()
    if a.question:
        ask(openai_client, toolbox, a.question, a.show_tools)
        return 0
    print("Ask about parcels (blank line to quit).")
    while True:
        q = input("> ").strip()
        if not q:
            return 0
        ask(openai_client, toolbox, q, a.show_tools)


if __name__ == "__main__":
    raise SystemExit(main())
