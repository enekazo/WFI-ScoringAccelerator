"""Ask ONE chain question through pinned-score-agent-fabric with OpenTelemetry tracing
exported to the Application Insights resource connected to the Foundry project.

The connection string is fetched at runtime from the project (Entra auth) and never
written to disk.  Usage:  python scripts/run_traced_question.py [C5]
Needs: pip install azure-monitor-opentelemetry
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]

from pinned_scoring import access_chain as ac  # noqa: E402
from pinned_scoring import foundry_agent as fa  # noqa: E402

import run_foundry_ontology_eval as ev  # noqa: E402


def main(qid: str = "C5") -> dict:
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry import trace

    project = fa.make_project_client()
    configure_azure_monitor(connection_string=project.telemetry.get_application_insights_connection_string())
    try:
        from azure.ai.projects.telemetry import AIProjectInstrumentor
        AIProjectInstrumentor().instrument()
    except Exception as exc:  # noqa: BLE001 - optional instrumentation
        print("AIProjectInstrumentor unavailable:", exc, file=sys.stderr)

    import fabric_load_chain as fl
    chain = ac.Chain(ac.generate(fl.local_parcels()))
    q = next(x for x in ac.questions(chain) if x.qid == qid)
    with trace.get_tracer("pinned-score").start_as_current_span(f"pinned-score-{qid}") as span:
        rec = ev.OntologyAgent().ask(f"{q.text}\n{ev.FORMAT_HINT}")
        trace_id = format(span.get_span_context().trace_id, "032x")
    out = {"qid": qid, "trace_id": trace_id, "answer": rec["answer"],
           "tool_calls": rec["tool_calls"], "error": rec["error"]}
    trace.get_tracer_provider().force_flush()
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "C5")
