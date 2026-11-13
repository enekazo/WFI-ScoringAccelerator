"""Record installer creation provenance in the cleanup ownership ledger.

deploy-all.ps1 exports PINNED_OWNERSHIP_LEDGER. When it is set, every call must succeed or raise, so a
ledger write failure stops the install instead of silently leaving resources that cleanup cannot own.
When it is unset (a standalone script run outside deploy-all) nothing is recorded and False is returned.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

LEDGER_ENV = "PINNED_OWNERSHIP_LEDGER"
_DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def ledger_path() -> Path | None:
    raw = os.environ.get(LEDGER_ENV, "").strip()
    return Path(raw) if raw else None


def record_owned(kind: str, id_: str, origin: str, evidence: str, name: str = "",
                 attrs: dict | None = None) -> bool:
    path = ledger_path()
    if path is None:
        return False
    if not id_:
        raise ValueError(f"cannot record a {kind} without an exact ID")
    if origin not in {"created", "reused"}:
        raise ValueError("origin must be 'created' or 'reused'")
    if str(_DEPLOY) not in sys.path:
        sys.path.insert(0, str(_DEPLOY))
    import cleanup_plan as cp

    ledger = cp.load_ledger(path)
    cp.record(ledger, kind, id_, origin, evidence, name, attrs or {})
    cp.validate_ledger(ledger)
    cp.save_ledger(ledger, path)
    return True


def arm_exists(url: str) -> bool:
    """GET an ARM URL via az rest: True if it exists, False only on a verified 404, otherwise raise."""
    import subprocess

    r = subprocess.run(["az", "rest", "--method", "get", "--url", url, "-o", "none"],
                       capture_output=True, text=True, shell=(os.name == "nt"))
    if r.returncode == 0:
        return True
    err = r.stderr or ""
    if str(_DEPLOY) not in sys.path:
        sys.path.insert(0, str(_DEPLOY))
    import cleanup_plan as cp

    if cp.is_missing_error(err):
        return False
    raise RuntimeError(f"cannot determine whether {url.split('?')[0]} exists: {err.strip()}")


def record_connection(url: str, existed: bool, connection_id: str) -> bool:
    return record_owned("foundry_connection", connection_id, "reused" if existed else "created",
                        "connection already existed before PUT" if existed else "connection PUT after GET returned 404")


def record_agent_version(endpoint: str, agent_name: str, version: str) -> bool:
    return record_owned("foundry_agent", agent_name, "created",
                        "agent version created by this install",
                        attrs={"endpoint": endpoint, "versions": [str(version)]})
