"""Minimal access chain: parcel -> access road -> homes / critical facilities -> water source -> crew access.

All data here is SYNTHETIC and seeded. Table shapes are modelled on the platform concepts
houses_exposed / distance_to_houses_m and contractor capability. The platform has no road, water or
access tables: road_segment, water_source and crew_access are new design/synthetic tables.
Every row carries provenance ('measured' or 'assumed') and the seed that generated it.
"""

import json
import random
import re
from dataclasses import dataclass

SEED = 20260715
TABLES = {
    "parcel": ["parcel_id", "status", "band", "score", "provenance", "seed"],
    "road_segment": ["road_id", "parcel_id", "passable", "width_class", "provenance", "seed"],
    "home_group": ["group_id", "parcel_id", "houses", "has_critical_facility", "distance_to_houses_m",
                   "provenance", "seed"],
    "water_source": ["water_id", "road_id", "type", "capacity_l", "provenance", "seed"],
    "crew_access": ["parcel_id", "crew_type", "reachable", "via_road_id", "provenance", "seed"],
}
RELATIONSHIPS = {  # name -> (from table.column, to table.column)
    "parcel_has_road": ("parcel.parcel_id", "road_segment.parcel_id"),
    "road_has_water": ("road_segment.road_id", "water_source.road_id"),
    "parcel_has_homes": ("parcel.parcel_id", "home_group.parcel_id"),
    "parcel_has_crew_access": ("parcel.parcel_id", "crew_access.parcel_id"),
}
CREW_TYPES = ("engine", "hand_crew", "dozer")
HIGH_BANDS = ("high", "extreme")


def _prov(rng) -> str:
    return "measured" if rng.random() < 0.7 else "assumed"


def generate(parcels: list, seed: int = SEED) -> dict:
    """parcels: [{parcel_id, status, band, score}] -> {table: [row dict]}. Deterministic."""
    rng = random.Random(seed)
    t = {k: [] for k in TABLES}
    for p in sorted(parcels, key=lambda p: p["parcel_id"]):
        pid = p["parcel_id"]
        t["parcel"].append({"parcel_id": pid, "status": p["status"], "band": p["band"],
                            "score": p["score"], "provenance": "measured", "seed": seed})
        roads = []
        for i in range(rng.choice((0, 1, 1, 2))):
            roads.append({"road_id": f"R-{pid}-{i + 1}", "parcel_id": pid,
                          "passable": rng.random() < 0.7,
                          "width_class": rng.choice(("narrow", "standard", "wide")),
                          "provenance": _prov(rng), "seed": seed})
        t["road_segment"] += roads
        for i in range(rng.choice((0, 1, 1, 2))):
            t["home_group"].append({
                "group_id": f"H-{pid}-{i + 1}", "parcel_id": pid, "houses": rng.randint(2, 40),
                "has_critical_facility": rng.random() < 0.2,
                "distance_to_houses_m": rng.randint(20, 600), "provenance": _prov(rng), "seed": seed})
        for r in roads:
            if rng.random() < 0.5:
                t["water_source"].append({
                    "water_id": f"W-{r['road_id']}", "road_id": r["road_id"],
                    "type": rng.choice(("hydrant", "pond", "tank", "dip_site")),
                    "capacity_l": rng.choice((5000, 10000, 20000, 50000)),
                    "provenance": _prov(rng), "seed": seed})
        ok = [r for r in roads if r["passable"]]
        rules = {"engine": [r for r in ok if r["width_class"] != "narrow"],
                 "hand_crew": ok,
                 "dozer": [r for r in ok if r["width_class"] == "wide"]}
        for crew in CREW_TYPES:
            via = rules[crew][0] if rules[crew] else None
            t["crew_access"].append({
                "parcel_id": pid, "crew_type": crew, "reachable": via is not None,
                "via_road_id": via["road_id"] if via else None,
                "provenance": "assumed", "seed": seed})  # derived by rule from road attributes
    return t


class Chain:
    def __init__(self, tables: dict):
        self.t = tables
        self.parcels = {r["parcel_id"]: r for r in tables["parcel"]}

    def rows(self, table, **eq):
        return [r for r in self.t[table] if all(r.get(k) == v for k, v in eq.items())]

    def roads(self, pid):
        return self.rows("road_segment", parcel_id=pid)

    def passable_roads(self, pid):
        return [r for r in self.roads(pid) if r["passable"]]

    def homes(self, pid):
        return self.rows("home_group", parcel_id=pid)

    def water(self, pid, passable_only=True):
        roads = self.passable_roads(pid) if passable_only else self.roads(pid)
        return [w for r in roads for w in self.rows("water_source", road_id=r["road_id"])]

    def crew(self, pid):
        return self.rows("crew_access", parcel_id=pid)

    def houses(self, pid):
        return sum(h["houses"] for h in self.homes(pid))

    def support(self, pid):
        """All rows that a chain answer about this parcel rests on."""
        return (self.roads(pid) + self.homes(pid) + self.water(pid, False) + self.crew(pid))


def weakest(rows) -> str:
    return "assumed" if any(r["provenance"] == "assumed" for r in rows) else "measured"


@dataclass(frozen=True)
class ChainQuestion:
    qid: str
    text: str
    kind: str  # "set" -> answer.parcels, "one" -> answer.parcels == [id]


def questions(chain: Chain) -> list:
    """5 questions. The parcel for Q3 is picked in code: the first parcel with a crew type that cannot reach it."""
    q3 = next((p for p in sorted(chain.parcels) if any(not c["reachable"] for c in chain.crew(p))
               and any(c["reachable"] for c in chain.crew(p))), next(iter(sorted(chain.parcels))))
    return [
        ChainQuestion("C1", "Which parcels have homes but no passable access road?", "set"),
        ChainQuestion("C2", "Which high-risk parcels (band high or extreme) have no water source on a "
                            "passable access road?", "set"),
        ChainQuestion("C3", f"Which crew types cannot reach parcel {q3}? Put the crew types in 'parcels'.",
                      "set"),
        ChainQuestion("C4", "Which single parcel has the most homes among parcels that have a water "
                            "source on a passable access road? Give its id in 'parcels' and the number "
                            "of homes in 'value'.", "one"),
        ChainQuestion("C5", "Which parcels have a critical facility but no passable access road that an "
                            "engine can use?", "set"),
    ]


def truth(chain: Chain, q: ChainQuestion) -> dict:
    """{parcels: sorted ids (or crew types), value, basis} computed in code from the tables."""
    ids = sorted(chain.parcels)
    value = None
    if q.qid == "C1":
        ans = [p for p in ids if chain.houses(p) > 0 and not chain.passable_roads(p)]
    elif q.qid == "C2":
        ans = [p for p in ids if chain.parcels[p]["band"] in HIGH_BANDS and not chain.water(p)]
    elif q.qid == "C3":
        pid = re.search(r"parcel (P\d+)", q.text).group(1)
        ans = sorted(c["crew_type"] for c in chain.crew(pid) if not c["reachable"])
        return {"parcels": ans, "value": None, "basis": weakest(chain.crew(pid))}
    elif q.qid == "C4":
        cand = [(chain.houses(p), p) for p in ids if chain.water(p)]
        if not cand:
            return {"parcels": [], "value": None, "basis": "measured"}
        best = max(h for h, _ in cand)
        pick = sorted(p for h, p in cand if h == best)[0]
        return {"parcels": [pick], "value": best,
                "basis": weakest(chain.homes(pick) + chain.roads(pick) + chain.water(pick, False))}
    elif q.qid == "C5":
        ans = [p for p in ids if any(h["has_critical_facility"] for h in chain.homes(p))
               and not any(c["reachable"] for c in chain.crew(p) if c["crew_type"] == "engine")]
    else:
        raise KeyError(q.qid)
    rows_for = {"C1": lambda p: chain.roads(p) + chain.homes(p),
                "C2": lambda p: chain.roads(p) + chain.water(p, False),
                "C5": lambda p: chain.homes(p) + chain.crew(p)}[q.qid]
    rows = [r for p in ans for r in rows_for(p)]
    return {"parcels": ans, "value": value, "basis": weakest(rows) if rows else "measured"}


SYSTEM = (
    "You answer questions about wildfire access for a set of synthetic parcels. Use ONLY tool results; "
    "never guess. Reply with one JSON object and nothing else: "
    '{"parcels": [<parcel ids, or crew type names for crew questions>], "value": <number or null>, '
    '"basis": "measured" | "assumed", "note": "<one sentence>"}. '
    "basis is 'assumed' if ANY row your answer rests on has provenance 'assumed', otherwise 'measured'."
)

NUM = re.compile(r"\d+(?:\.\d+)?")


def parse(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def grade(truth_ans: dict, answer) -> dict:
    if not isinstance(answer, dict):
        return {"correct": 0.0, "provenance_ok": 0.0}
    got = sorted(str(x) for x in answer.get("parcels") or [])
    ok = got == sorted(truth_ans["parcels"])
    if truth_ans["value"] is not None:
        ok = ok and answer.get("value") == truth_ans["value"]
    return {"correct": float(ok), "provenance_ok": float(answer.get("basis") == truth_ans["basis"])}


def numbers_pinned(answer, results, question_text) -> float:
    """Every number the answer states (value + note) must appear in a tool result or the question."""
    if not isinstance(answer, dict):
        return 0.0
    seen = set(NUM.findall(json.dumps(results, default=str))) | set(NUM.findall(question_text))
    said = set(NUM.findall(str(answer.get("value") or ""))) | set(NUM.findall(str(answer.get("note") or "")))
    return float(said <= seen)


GRAPH_TOOLS = [
    {"type": "function", "name": "chain_for_parcel",
     "description": "Everything on the chain for one parcel: band, roads, homes, water, crew access.",
     "parameters": {"type": "object", "properties": {"parcel_id": {"type": "string"}},
                    "required": ["parcel_id"]}},
    {"type": "function", "name": "find_parcels",
     "description": "Traverse the chain and list parcels matching ALL given filters (omit to ignore). "
                    "Returns id, band, houses, critical facility, passable roads, water on passable road, "
                    "engine reachable, and the weakest provenance of the supporting rows.",
     "parameters": {"type": "object", "properties": {
         "has_homes": {"type": "boolean"}, "has_critical_facility": {"type": "boolean"},
         "has_passable_road": {"type": "boolean"}, "has_water_on_passable_road": {"type": "boolean"},
         "engine_reachable": {"type": "boolean"}, "band_in": {"type": "array", "items": {"type": "string"}}}}},
]
FLAT_TOOLS = [
    {"type": "function", "name": "read_table",
     "description": "Rows of ONE table (parcel, road_segment, home_group, water_source, crew_access), "
                    "optionally filtered by equality on one column. No joins: combine tables yourself.",
     "parameters": {"type": "object", "properties": {
         "table": {"type": "string"}, "column": {"type": "string"}, "equals": {"type": "string"}},
         "required": ["table"]}},
]


class ChainTools:
    """Read-only tools over a Chain. graph=True exposes traversal tools; graph=False raw single-table reads."""

    def __init__(self, chain: Chain, graph: bool):
        self.c, self.graph = chain, graph

    def definitions(self):
        return GRAPH_TOOLS if self.graph else FLAT_TOOLS

    def _summary(self, p):
        c = self.c
        return {"parcel_id": p, "band": c.parcels[p]["band"], "houses": c.houses(p),
                "has_critical_facility": any(h["has_critical_facility"] for h in c.homes(p)),
                "passable_roads": len(c.passable_roads(p)), "water_on_passable_road": len(c.water(p)),
                "engine_reachable": any(r["reachable"] for r in c.crew(p) if r["crew_type"] == "engine"),
                "weakest_provenance": weakest(c.support(p))}

    def call(self, name, arguments):
        from .controls import kill_switch_engaged
        if kill_switch_engaged():
            return {"error": "kill switch engaged: tools are disabled"}
        c = self.c
        if name == "read_table" and not self.graph:
            tab = arguments.get("table")
            if tab not in TABLES:
                return {"error": f"unknown table {tab!r}", "tables": sorted(TABLES)}
            rows = c.t[tab]
            if arguments.get("column"):
                col, val = arguments["column"], arguments.get("equals")
                rows = [r for r in rows if str(r.get(col)).lower() == str(val).lower()]
            return {"table": tab, "rows": rows}
        if name == "chain_for_parcel" and self.graph:
            p = arguments.get("parcel_id")
            if p not in c.parcels:
                return {"error": f"unknown parcel {p!r}"}
            return {"parcel": c.parcels[p], "roads": c.roads(p), "homes": c.homes(p),
                    "water": c.water(p, False), "crew_access": c.crew(p)}
        if name == "find_parcels" and self.graph:
            out = []
            for p in sorted(c.parcels):
                s = self._summary(p)
                checks = {"has_homes": s["houses"] > 0, "has_critical_facility": s["has_critical_facility"],
                          "has_passable_road": s["passable_roads"] > 0,
                          "has_water_on_passable_road": s["water_on_passable_road"] > 0,
                          "engine_reachable": s["engine_reachable"]}
                if any(k in checks and checks[k] != v for k, v in arguments.items()
                       if isinstance(v, bool)):
                    continue
                if arguments.get("band_in") and s["band"] not in arguments["band_in"]:
                    continue
                out.append(s)
            return {"parcels": out}
        return {"error": f"unknown tool {name!r}"}
