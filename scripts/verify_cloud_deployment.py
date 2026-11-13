"""Live Function acceptance checks; no Foundry model calls or local scoring/verification."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pinned_scoring.cloud_tool_host import call_function_json, derive_function_base_url
from pinned_scoring.receipt import Receipt


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_deployment(endpoint: str, manifest: dict) -> dict:
    base = derive_function_base_url(endpoint)
    expected = {}
    for path in sorted(Path(manifest["receiptsPath"]).glob("P*.json")):
        receipt = Receipt.from_wire(json.loads(path.read_text(encoding="utf-8")))
        require(receipt.policy_sha256 == manifest["policySha256"], "Local receipt policy mismatch.")
        expected[receipt.entity_id] = receipt
    require(len(expected) == manifest["parcelCount"] > 0, "Local receipt coverage mismatch.")

    with urllib.request.urlopen(f"{base}/api/health", timeout=60) as response:
        health = json.load(response)
    require(health.get("source") == "blob", "Function is not using Blob receipts.")
    require(health.get("authRequired") is True, "Function authentication is disabled.")
    require(health.get("receiptsLoaded") == len(expected), "Function receipt count mismatch.")
    require(health.get("receiptsPrefix") == manifest["receiptsPrefix"], "Function namespace mismatch.")
    require(health.get("policySha256") == manifest["policySha256"], "Function pinned policy mismatch.")

    for route in ("mcp", "score", "verify"):
        request = urllib.request.Request(
            f"{base}/api/{route}", data=b"{}", headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        require(status == 401, f"Unauthenticated /api/{route} returned {status}, expected 401.")

    scored = call_function_json(
        base, "/api/score",
        {"parcels": [], "submitted_policy_sha256": manifest["policySha256"]}, timeout=120,
    )
    rows = scored.get("receipts", [])
    require(scored.get("count") == len(expected) == len(rows), "Remote scoring coverage mismatch.")
    actual = {}
    for wire in rows:
        receipt = Receipt.from_wire(wire)
        require(receipt.entity_id not in actual, "Remote scorer returned a duplicate parcel.")
        actual[receipt.entity_id] = receipt
    require(actual.keys() == expected.keys(), "Remote scorer returned different parcel IDs.")
    require(
        all(actual[key].receipt_sha256 == value.receipt_sha256 for key, value in expected.items()),
        "Function scores do not reproduce the prepared receipts.",
    )

    primary = next((r for r in expected.values() if r.status == "scored"), next(iter(expected.values())))
    truthful = (
        f"Parcel {primary.entity_id} has score {primary.score} and band {primary.band}."
        if primary.status == "scored" else f"Parcel {primary.entity_id} is incomplete."
    )
    results = {}
    for label, text, blocked, mode in (
        ("normal", truthful, False, "verified_narration"),
        ("contradiction", f"Parcel {primary.entity_id} has score 999999.", True, "receipt_fallback"),
    ):
        result = call_function_json(
            base, "/api/verify",
            {"question": f"Explain parcel {primary.entity_id}.", "answer": text,
             "entity_id": primary.entity_id},
        )
        delivered = result.get("delivered", {})
        require(result.get("blocked") is blocked, f"{label}: wrong blocked result.")
        require(result.get("fallback_used") is blocked, f"{label}: wrong fallback result.")
        require(delivered.get("mode") == mode, f"{label}: wrong delivery mode.")
        require(bool(result.get("text")), f"{label}: missing delivered text.")
        require(
            result.get("receipt_hashes", {}).get(primary.entity_id) == primary.receipt_sha256,
            f"{label}: wrong receipt provenance.",
        )
        require(result.get("policy_sha256") == manifest["policySha256"], f"{label}: wrong policy.")
        require(not blocked or "999999" not in result["text"], "Rejected narration was delivered.")
        results[label] = result
    return {
        "ok": True, "functionEndpoint": base, "parcelCount": len(expected),
        "policySha256": manifest["policySha256"], "receiptsPrefix": manifest["receiptsPrefix"],
        "unauthenticatedStatus": 401, "scoreHashParity": True, "verification": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function-endpoint", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('{"ok": false, "status": "in_progress"}\n', encoding="utf-8")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        result = check_deployment(args.function_endpoint, manifest)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
