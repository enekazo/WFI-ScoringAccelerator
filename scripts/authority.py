"""Authority plane CLI (Fabric only): propose -> review -> approve, with reject, status, verify.

  python scripts/authority.py propose P126
  python scripts/authority.py review <proposal_id> [--as upn] [--note text]
  python scripts/authority.py approve <proposal_id> [--as upn]
  python scripts/authority.py reject <proposal_id> --note "reason"
  python scripts/authority.py status [proposal_id]
  python scripts/authority.py verify

Roles come from examples/wildfire/roles.json (copy roles.example.json; never commit it).
review/approve/reject act as the signed-in Entra user (az account show) unless --as is given.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pinned_scoring import foundry_agent as fa  # noqa: E402
from pinned_scoring.authority import (AuthorityError, AuthorityLog, FabricDecisionStore,  # noqa: E402
                                      Roles, signed_in_user)

ROLES_PATH = ROOT / "examples" / "wildfire" / "roles.json"


def make_log(args) -> AuthorityLog:
    return AuthorityLog(FabricDecisionStore(), Roles.load(args.roles), max_age_days=args.max_age_days)


def propose(args, log) -> dict:
    os.environ.setdefault(fa.DATA_SOURCE_ENV, "fabric")
    from run_e2e_foundry import build_contexts
    ctx, _ = build_contexts(os.environ[fa.DATA_SOURCE_ENV])
    reader = fa.make_reader()
    box = fa.AuthorityToolbox(ctx, log, reader=reader)
    project = fa.make_project_client()
    trace = []
    text = fa.run_agent_turn(
        project.get_openai_client(), box,
        f"Propose an action for parcel {args.parcel}. Read its receipt first, then call "
        f"propose_action with your explanation.", trace=trace)
    return {"agent_answer": text, "tool_calls": [t["tool"] for t in trace]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roles", default=str(ROLES_PATH))
    ap.add_argument("--max-age-days", type=int, default=7,
                    help="freshness gate on the receipt's as_of (default 7)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("parcel")
    for name in ("review", "approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("proposal_id")
        p.add_argument("--as", dest="person")
        p.add_argument("--note", default="")
    p = sub.add_parser("status")
    p.add_argument("proposal_id", nargs="?")
    sub.add_parser("verify")
    args = ap.parse_args()
    try:
        log = make_log(args)
        if args.cmd == "propose":
            out = propose(args, log)
        elif args.cmd in ("review", "approve"):
            out = log.advance(args.proposal_id, args.person or signed_in_user(), args.note)
        elif args.cmd == "reject":
            out = log.reject(args.proposal_id, args.person or signed_in_user(), args.note)
        elif args.cmd == "verify":
            log.verify_chain()
            out = {"ok": True, "entries": len(log.entries()),
                   "separation_of_duties_configured": log.roles.mode,
                   "modes_recorded_in_log": log.modes_used()}
        else:
            if args.proposal_id:
                out = {"separation_of_duties_configured": log.roles.mode,
                       "state": log.state(args.proposal_id), "history": log.history(args.proposal_id)}
            else:
                latest = {}
                for e in log.entries():
                    latest[e["proposal_id"]] = {"state": e["state"], "entity_id": e["entity_id"],
                                                "by": e["actor"], "at": e["at"],
                                                "mode": e.get("mode") or "n/a (proposal)"}
                out = {"separation_of_duties_configured": log.roles.mode, "proposals": latest}
    except AuthorityError as exc:
        print(json.dumps({"blocked": str(exc)}, indent=1))
        return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
