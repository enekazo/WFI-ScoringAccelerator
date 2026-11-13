import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_plan
import fabric_graph_model_fix
import fabric_hash_check
import pin_policy
from pinned_scoring.access_chain import Chain, generate as generate_chain, questions, truth
from pinned_scoring.cloud_tool_host import BlobReceiptReader
from pinned_scoring.receipt import Receipt
from pinned_scoring.synth.generator import generate, generate_deployment, generate_extra


@pytest.mark.parametrize("count", [1, 25, 50, 150, 251])
def test_deployment_dataset_has_exact_count_and_preserves_recorded_inputs(count):
    dataset = generate_deployment(count)
    assert len(dataset.observations) == count
    assert set(dataset.observations) == {f"P{i:03d}" for i in range(1, count + 1)}
    original = generate(20260715)
    extra = generate_extra()
    for pid, rows in dataset.observations.items():
        if pid in original.observations:
            assert rows == original.observations[pid]
        elif pid in extra.observations:
            assert rows == extra.observations[pid]
    assert dataset == generate_deployment(count)


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "150", None])
def test_invalid_parcel_count_is_rejected(value):
    with pytest.raises(ValueError, match="positive integer"):
        generate_deployment(value)
    params = json.loads((ROOT / "deploy/parameters.template.json").read_text())
    params["parcelCount"] = value
    result = deploy_plan.validate_parameters(params, allow_template_guids=True)
    assert not result["ok"]
    assert any("parcelCount" in error for error in result["errors"])


@pytest.mark.parametrize("count", [1, 50, 150])
def test_preparation_shares_exact_dataset_and_policy_with_fabric(tmp_path, count):
    params = tmp_path / "parameters.json"
    params.write_text(json.dumps({
        "parcelCount": count,
        "fabricWorkspaceName": "Target",
        "fabricCapacityName": "TargetCap",
    }))
    policy_path = ROOT / "examples/wildfire/policy.json"
    manifest = pin_policy.prepare_data(policy_path, params, tmp_path / "out")
    assert manifest["parcelCount"] == count
    receipts = [
        Receipt.from_wire(json.loads(p.read_text()))
        for p in Path(manifest["receiptsPath"]).glob("P*.json")
    ]
    assert len(receipts) == count
    assert all(r.policy_sha256 == manifest["policySha256"] for r in receipts)
    graph = json.loads(Path(manifest["graphPath"]).read_text())
    assert len(graph["parcels"]) == count
    fabric = json.loads(Path(manifest["fabricParametersPath"]).read_text())
    assert fabric["workspaceName"] == "Target"
    assert fabric["capacityName"] == "TargetCap"
    assert fabric["capacityId"] == ""
    for key in ("parcelCount", "policyPath", "datasetPath", "receiptsPath", "graphPath"):
        assert fabric[key] == manifest[key]
    chain = Chain(generate_chain([
        {"parcel_id": r.entity_id, "status": r.status, "band": r.band, "score": str(r.score)}
        for r in receipts
    ]))
    assert len(chain.t["crew_access"]) == 3 * count
    assert len([truth(chain, q) for q in questions(chain)]) == 5
    assert fabric_hash_check.compare(receipts, Path(manifest["receiptsPath"]))["ok"]
    assert not fabric_hash_check.compare(receipts[:-1], Path(manifest["receiptsPath"]))["ok"]


def test_count_reduction_uses_a_different_blob_prefix_and_receipt_directory(tmp_path):
    params = tmp_path / "parameters.json"
    manifests = []
    for count in (150, 25):
        params.write_text(json.dumps({"parcelCount": count}))
        manifests.append(pin_policy.prepare_data(
            ROOT / "examples/wildfire/policy.json", params, tmp_path / "out"
        ))
    assert manifests[0]["receiptsPrefix"] != manifests[1]["receiptsPrefix"]
    assert manifests[0]["receiptsPath"] != manifests[1]["receiptsPath"]
    assert len(list(Path(manifests[1]["receiptsPath"]).glob("P*.json"))) == 25
    assert len(list(Path(manifests[0]["receiptsPath"]).glob("P*.json"))) == 150


def test_blob_reader_respects_deployment_prefix(monkeypatch):
    monkeypatch.setenv("RECEIPTS_PREFIX", "deployments/current/receipts")
    reader = BlobReceiptReader(container_client=object())
    assert reader._blob_name("P150") == "deployments/current/receipts/P150.json"
    override = BlobReceiptReader(container_client=object(), prefix="another")
    assert override._blob_name("P001") == "another/P001.json"


def test_graph_verification_runs_fresh_queries_for_configured_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("PINNED_VERIFICATION_DIR", str(tmp_path))
    payload = {
        "dataset_matches": True,
        "questions": {
            qid: {"gql_answer": ["P150"], "truth": ["P150"], "match": True,
                  "gql_value": 7 if qid == "C4" else None,
                  "truth_value": 7 if qid == "C4" else None}
            for qid in ("C1", "C2", "C3", "C4", "C5")
        },
    }

    def run(args, **kwargs):
        assert Path(args[1]).name == "fabric_graph_chain.py"
        assert args[-2:] == ["--graph-model-id", "target-graph"]
        (tmp_path / "fabric_graph_chain_gql.json").write_text(json.dumps(payload))

    monkeypatch.setattr(fabric_graph_model_fix.subprocess, "run", run)
    args = ["--workspace-id", "target-workspace", "--graph-model-id", "target-graph", "--verify-c1-c5"]
    assert fabric_graph_model_fix.main(args) == 0
    payload["questions"]["C4"]["gql_value"] = 8
    assert fabric_graph_model_fix.main(args) == 1


def test_resolve_ontology_tags_and_patch_graph_model_parts_syncs_relationship_lineage_tags(monkeypatch):
    import base64

    monkeypatch.delenv("FABRIC_LAKEHOUSE_ID", raising=False)
    rel_tmdl = "\n".join([
        "entityRelationship 'Road Serves Parcel'",
        "\tlineageTag: 8dcc0941-d4b7-4e54-be96-0a39079ab507",
        "\tfromEntity: RoadSegment",
        "\ttoEntity: Parcel",
        "\tbackingConfiguration",
        "\t\trelationship: parcel_has_road",
        "entityRelationship 'Crew Access To Parcel'",
        "\tlineageTag: 60fd1200-6701-4f40-b604-090475241503",
        "\tfromEntity: CrewAccess",
        "\ttoEntity: Parcel",
        "\tbackingConfiguration",
        "\t\trelationship: parcel_has_crew_access",
    ])
    table_tmdl = "table parcel\n\tannotation ONT_ItemId = \"lh-from-tmdl\"\n"
    ont_def = {
        "definition": {
            "parts": [
                {
                    "path": "entities/Parcel.tmdl",
                    "payload": base64.b64encode(b"entity Parcel\n\tlineageTag: ent-parcel-1\n").decode("ascii"),
                },
                {
                    "path": "entityRelationships.tmdl",
                    "payload": base64.b64encode(rel_tmdl.encode("utf-8")).decode("ascii"),
                },
                {
                    "path": "tables/parcel.tmdl",
                    "payload": base64.b64encode(table_tmdl.encode("utf-8")).decode("ascii"),
                },
            ]
        }
    }
    monkeypatch.setattr(
        fabric_graph_model_fix,
        "fabric_call",
        lambda method, path, body=None: (200, {}, json.dumps(ont_def)),
    )
    lh, et, rt = fabric_graph_model_fix.resolve_ontology_tags_and_lakehouse("ws-1", ontology_id="ont-1")
    assert lh == "lh-from-tmdl"
    assert et["Parcel"] == "ent-parcel-1"
    assert rt["parcel_has_road"] == "8dcc0941-d4b7-4e54-be96-0a39079ab507"
    assert rt["parcel_has_crew_access"] == "60fd1200-6701-4f40-b604-090475241503"

    # Build stale GraphModel parts with fallback relationship tags, then verify patch_graph_model_parts updates them.
    stale_ds, stale_gt, stale_gd = fabric_graph_model_fix.build_default_graph_model_objects("ws-1", "lh-from-tmdl")
    parts = [
        {"path": "dataSources.json", "payload": base64.b64encode(json.dumps(stale_ds).encode()).decode("ascii")},
        {"path": "graphType.json", "payload": base64.b64encode(json.dumps(stale_gt).encode()).decode("ascii")},
        {"path": "graphDefinition.json", "payload": base64.b64encode(json.dumps(stale_gd).encode()).decode("ascii")},
    ]
    patched_parts, summary = fabric_graph_model_fix.patch_graph_model_parts(
        parts, workspace_id="ws-1", lakehouse_id=lh, entity_tags=et, rel_tags=rt
    )
    assert summary["graphType_patched"] is True
    assert summary["graphDefinition_patched"] is True
    gt_out = json.loads(base64.b64decode(patched_parts[1]["payload"]).decode("utf-8"))
    edge_by_label = {e["labels"][0]: e["alias"] for e in gt_out["edgeTypes"]}
    assert edge_by_label["parcel_has_road"] == "8dcc0941-d4b7-4e54-be96-0a39079ab507"
    assert edge_by_label["parcel_has_crew_access"] == "60fd1200-6701-4f40-b604-090475241503"

