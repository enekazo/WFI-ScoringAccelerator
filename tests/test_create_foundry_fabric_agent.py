import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import create_foundry_fabric_agent as c  # noqa: E402


def test_connection_body_and_url():
    b = c.build_connection_body("ws", "da")
    assert b["properties"]["credentials"]["keys"] == {"workspace_id": "ws", "artifact_id": "da"}
    u = c.connection_url("s", "rg", "acct", "proj")
    assert "/accounts/acct/projects/proj/connections/" in u and "api-version=" in u


def test_agent_name_is_separate():
    assert c.AGENT_NAME == "pinned-score-agent-fabric"


def test_iq_connection_body():
    p = c.build_iq_connection_body("ws", "onto")["properties"]
    assert p["category"] == "RemoteTool" and p["authType"] == "UserEntraToken"
    assert p["target"].endswith("/workspaces/ws/items/onto/ontologyEndpoint")


def test_grade_ontology_answer_rejects_ungrounded_and_partial_narrow_road_c5():
    from pinned_scoring import access_chain as ac

    parcels = [
        {"parcel_id": "P020", "status": "scored", "band": "high", "score": "72.0"},
        {"parcel_id": "P087", "status": "scored", "band": "moderate", "score": "48.0"},
    ]
    tables = {
        "parcel": [{**p, "provenance": "measured", "seed": 1} for p in parcels],
        "road_segment": [
            {"road_id": "R020_1", "parcel_id": "P020", "passable": False, "width_class": "standard", "provenance": "measured", "seed": 1},
            {"road_id": "R087_1", "parcel_id": "P087", "passable": True, "width_class": "narrow", "provenance": "measured", "seed": 1},
        ],
        "home_group": [
            {"group_id": "H020_1", "parcel_id": "P020", "houses": 2, "has_critical_facility": True, "distance_to_houses_m": 50, "provenance": "measured", "seed": 1},
            {"group_id": "H087_1", "parcel_id": "P087", "houses": 3, "has_critical_facility": True, "distance_to_houses_m": 60, "provenance": "measured", "seed": 1},
        ],
        "water_source": [],
        "crew_access": [
            {"parcel_id": "P020", "crew_type": "hand_crew", "reachable": False, "via_road_id": None, "provenance": "measured", "seed": 1},
            {"parcel_id": "P020", "crew_type": "engine", "reachable": False, "via_road_id": None, "provenance": "measured", "seed": 1},
            {"parcel_id": "P020", "crew_type": "dozer", "reachable": False, "via_road_id": None, "provenance": "measured", "seed": 1},
            {"parcel_id": "P087", "crew_type": "hand_crew", "reachable": True, "via_road_id": "R087_1", "provenance": "measured", "seed": 1},
            {"parcel_id": "P087", "crew_type": "engine", "reachable": False, "via_road_id": None, "provenance": "measured", "seed": 1},
            {"parcel_id": "P087", "crew_type": "dozer", "reachable": False, "via_road_id": None, "provenance": "measured", "seed": 1},
        ],
    }
    chain = ac.Chain(tables)
    c5 = [q for q in ac.questions(chain) if q.qid == "C5"][0]
    c5_truth = ac.truth(chain, c5)
    assert c5_truth["parcels"] == ["P020", "P087"]

    ungrounded = c.grade_ontology_answer(
        "I cannot determine which parcels qualify because the required parcel, critical facility, and road accessibility information is not modeled or available.",
        c5_truth,
        qid="C5",
    )
    assert ungrounded["matched"] is False
    assert ungrounded["reason"] == "empty_or_ungrounded"

    partial_no_narrow = c.grade_ontology_answer(
        "Matching parcels: P020 (omitting narrow-road parcel).",
        c5_truth,
        qid="C5",
    )
    assert partial_no_narrow["matched"] is False
    assert partial_no_narrow["actual_parcels"] == ["P020"]

    complete = c.grade_ontology_answer(
        "The matching parcels are P020 and P087.",
        c5_truth,
        qid="C5",
    )
    assert complete["matched"] is True
    assert complete["actual_parcels"] == ["P020", "P087"]


def test_grade_ontology_answer_checks_c3_crews_and_c4_value():
    c3_truth = {"parcels": ["dozer", "engine"], "value": None}
    assert c.grade_ontology_answer("Neither engine nor dozer can reach parcel P001.", c3_truth, qid="C3")["matched"] is True
    assert c.grade_ontology_answer("Only dozer cannot reach parcel P001.", c3_truth, qid="C3")["matched"] is False

    c4_truth = {"parcels": ["P005"], "value": 7}
    assert c.grade_ontology_answer("Parcel P005 has the most homes with 7 homes.", c4_truth, qid="C4")["matched"] is True
    assert c.grade_ontology_answer("Parcel P005 has the most homes with 5 homes.", c4_truth, qid="C4")["matched"] is False


def test_ontology_define_chain_preserves_existing_lineage_tags_and_updates_docs():
    import fabric_ontology_define_chain as odc

    existing = {
        "expressions.tmdl": "expression DatabaseQuery =\n\tlineageTag: keep-expr-tag\n",
        "entities/CrewAccess.tmdl": (
            "/// Old doc\nentity CrewAccess\n\tlineageTag: keep-entity-tag\n\tbackingTable: crew_access\n"
            "\tkeyProperty: parcel_id\n\tkeyProperty: crew_type\n"
        ),
    }
    generated = odc.parts(existing=existing)
    assert "keep-expr-tag" in generated["expressions.tmdl"]
    assert "keep-entity-tag" in generated["entities/CrewAccess.tmdl"]
    assert "reachable=true" in generated["entities/CrewAccess.tmdl"]
    assert "width_class" in generated["entities/RoadSegment.tmdl"]

