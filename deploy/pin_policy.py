"""Deploy-time helpers: pin the policy and run a scorer + receipt smoke test.

  python deploy/pin_policy.py pin <policy> <out> <overrides-json>   -> prints SHA-256
  python deploy/pin_policy.py smoke <policy> <receipt-out>          -> writes receipt JSON
"""

import json
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pinned_scoring.contracts import Observation  # noqa: E402
from pinned_scoring.overrides import apply_overrides, write_policy  # noqa: E402
from pinned_scoring.policy import Policy  # noqa: E402
from pinned_scoring.receipt import Receipt  # noqa: E402
from pinned_scoring.scorer import score  # noqa: E402


def pin(policy_path, out_path, overrides_json):
    policy = apply_overrides(Policy.load(policy_path), json.loads(overrides_json or "{}"))
    print(write_policy(policy, out_path))


def smoke(policy_path, receipt_path):
    policy = Policy.load(policy_path)
    now = datetime.now(timezone.utc)
    rows = []
    for factor in policy.factors:
        mid = (factor.low + factor.high) / Decimal(2)
        rows.append(Observation(
            "smoke-test", factor.metric, mid, factor.unit,
            now - timedelta(minutes=5), now + timedelta(hours=1),
            factor.allowed_provenance[0].value, source="deploy-smoke-test",
        ))
    receipt = score("smoke-test", rows, policy, now)
    if receipt.status != "scored":
        raise SystemExit(f"smoke test did not score: {receipt.reasons}")
    wire = receipt.wire()
    Receipt.from_wire(wire)  # self-verification
    Path(receipt_path).write_text(json.dumps(wire, indent=2), encoding="utf-8")
    print(f"{receipt.score} {receipt.band} {receipt.receipt_sha256}")


def build_all(policy_path, dataset_path, out_dir):
    """Score all parcels in dataset.json against the pinned policy and write <parcel>.json files."""
    from pinned_scoring.fabric.loader import load_raw, read_rows
    from pinned_scoring.fabric.transforms import raw_to_silver, silver_to_gold

    policy = Policy.load(policy_path)
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    as_of = datetime.fromisoformat(dataset["as_of"]) if isinstance(dataset, dict) and "as_of" in dataset else now
    raw = load_raw(read_rows(dataset_path), "deploy-batch", now)
    silver = raw_to_silver(raw, policy, as_of)
    gold = silver_to_gold(silver, policy, as_of, scored_at=now)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for row in gold:
        wire = {
            "entity_id": row["entity_id"],
            "status": row["status"],
            "score": row["score"],
            "band": row["band"],
            "components": json.loads(row["components"]),
            "reasons": json.loads(row["reasons"]),
            "as_of": row["as_of"],
            "policy_sha256": row["policy_sha256"],
            "input_sha256": row["input_sha256"],
            "receipt_sha256": row["receipt_sha256"],
        }
        Receipt.from_wire(wire)
        (out / f"{row['entity_id']}.json").write_text(
            json.dumps(wire, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    print(f"built {len(gold)} receipts in {out}")
    return len(gold)


def count(parameters_file):
    from pinned_scoring.synth.generator import validate_parcel_count

    params = json.loads(Path(parameters_file).read_text(encoding="utf-8-sig"))
    print(validate_parcel_count(params.get("parcelCount", 150)))


def prepare_data(policy_path, parameters_file, out_dir):
    from pinned_scoring.graph import build_graph
    from pinned_scoring.synth.generator import (
        dataset_document, generate_deployment, validate_parcel_count, write_dataset,
    )

    params = json.loads(Path(parameters_file).read_text(encoding="utf-8-sig"))
    parcel_count = validate_parcel_count(params.get("parcelCount", 150))
    dataset = generate_deployment(parcel_count)
    policy = Policy.load(policy_path)
    digest = hashlib.sha256(
        json.dumps(dataset_document(dataset), sort_keys=True).encode("utf-8")
    ).hexdigest()
    directory = Path(out_dir).resolve() / f"data-{digest[:16]}-{policy.sha256[:16]}"
    write_dataset(dataset, directory)
    receipts = directory / "receipts"
    built = build_all(policy_path, directory / "dataset.json", receipts)
    if built != parcel_count or len(list(receipts.glob("P*.json"))) != parcel_count:
        raise ValueError(f"Expected {parcel_count} receipts, built {built}.")
    graph_path = directory / "graph.json"
    graph_path.write_text(
        json.dumps(build_graph(dataset.observations).to_doc(), indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "parcelCount": parcel_count,
        "datasetSha256": digest,
        "policySha256": policy.sha256,
        "policyPath": str(Path(policy_path).resolve()),
        "datasetPath": str(directory / "dataset.json"),
        "receiptsPath": str(receipts),
        "graphPath": str(graph_path),
        "receiptsPrefix": f"deployments/{digest}/{policy.sha256}/receipts",
        "fabricParametersPath": str(Path(out_dir).resolve() / "fabric.parameters.resolved.json"),
    }
    fabric_params = {
        "workspaceId": params.get("fabricWorkspaceId") or params.get("workspaceId", ""),
        "lakehouseId": params.get("fabricLakehouseId") or params.get("lakehouseId", ""),
        "ontologyId": params.get("fabricOntologyId") or params.get("ontologyId", ""),
        "graphModelId": params.get("fabricGraphModelId") or params.get("graphModelId", ""),
        "sqlServer": params.get("fabricSqlServer") or params.get("sqlServer", ""),
        "workspaceName": params.get("fabricWorkspaceName") or params.get("workspaceName", "PinnedScore"),
        "lakehouseName": params.get("fabricLakehouseName") or params.get("lakehouseName", "pinned_scoring_lh"),
        "ontologyName": params.get("fabricOntologyName") or params.get("ontologyName", "pinned_scoring_ontology"),
        "capacityName": params.get("fabricCapacityName") or params.get("capacityName", ""),
        "capacityId": params.get("fabricCapacityId") or params.get("capacityId", ""),
        "capacitySku": params.get("fabricCapacitySku") or params.get("capacitySku", "F4"),
        **{key: manifest[key] for key in ("parcelCount", "policyPath", "datasetPath", "receiptsPath", "graphPath")},
    }
    Path(manifest["fabricParametersPath"]).write_text(
        json.dumps(fabric_params, indent=2) + "\n", encoding="utf-8"
    )
    manifest_path = Path(out_dir) / "deployment-data.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {parcel_count} parcels: {manifest_path}")
    return manifest


def package_func(repo_root, zip_out):
    """Create a deployment zip for cloud/tool_host bundling src/pinned_scoring alongside function_app.py."""
    import zipfile

    root = Path(repo_root)
    tool_host_dir = root / "cloud" / "tool_host"
    pkg_dir = root / "src" / "pinned_scoring"
    zip_path = Path(zip_out)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(tool_host_dir.iterdir()):
            if f.is_file() and f.name not in {"local.settings.json", ".funcignore"} and not f.name.endswith(".pyc"):
                zf.write(f, arcname=f.name)
        for f in sorted(pkg_dir.rglob("*.py")):
            rel = f.relative_to(root / "src")
            zf.write(f, arcname=str(rel).replace("\\", "/"))
    print(str(zip_path))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {"pin": pin, "smoke": smoke, "build_all": build_all, "package_func": package_func,
     "count": count, "prepare_data": prepare_data}[cmd](*args)
