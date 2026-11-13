"""In-repo asset graph: parcel -> asset -> feeder line -> customer groups / critical facilities.

Everything here is SYNTHETIC and seeded (illustrative, not utility data). The graph carries no
scores. Scores come only from scorer.py via receipts; the graph says what a score is connected to.
"""

import json
import random
from dataclasses import dataclass
from pathlib import Path

GRAPH_SEED = 20260716
FEEDER_COUNT = 12
ASSET_TYPES = ("pole", "transformer", "recloser", "switch")
FACILITY_KINDS = ("hospital", "school", "water_plant", "fire_station", "care_home")
# Synthetic planning assumption: one critical facility counts as this many customers.
FACILITY_WEIGHT = 250
# Synthetic, illustrative mitigation catalogue: scales one input metric of the parcel.
MITIGATIONS = (
    {"id": "thin_vegetation", "metric": "fuel_load", "factor": "0.6", "cost_usd": 18000},
    {"id": "targeted_grazing", "metric": "dead_fuel_pct", "factor": "0.7", "cost_usd": 6000},
    {"id": "moisture_mulch", "metric": "fuel_moisture_pct", "factor": "1.3", "cost_usd": 9000},
)
DEFAULT_GRAPH_PATH = Path(__file__).resolve().parents[2] / "data" / "graph.json"


@dataclass(frozen=True)
class Graph:
    seed: int
    parcels: tuple          # parcel ids
    assets: dict            # asset id -> {"parcel", "feeder", "type"}
    feeders: tuple          # feeder ids
    customer_groups: dict   # group id -> {"feeder", "customers", "facility"}
    mitigations: tuple

    # ---- read-only traversals ----
    def assets_of(self, parcel: str) -> list:
        return sorted(a for a, v in self.assets.items() if v["parcel"] == parcel)

    def feeders_of(self, parcel: str) -> list:
        return sorted({self.assets[a]["feeder"] for a in self.assets_of(parcel)})

    def parcels_on(self, feeder: str) -> list:
        return sorted({v["parcel"] for v in self.assets.values() if v["feeder"] == feeder})

    def groups_on(self, feeder: str) -> list:
        return sorted(g for g, v in self.customer_groups.items() if v["feeder"] == feeder)

    def feeder_load(self, feeder: str) -> dict:
        groups = [self.customer_groups[g] for g in self.groups_on(feeder)]
        return {"customers": sum(g["customers"] for g in groups),
                "critical_facilities": sum(1 for g in groups if g["facility"])}

    def exposure(self, feeders) -> dict:
        """Distinct feeders -> customers and facilities (never double counted)."""
        feeders = sorted(set(feeders))
        loads = [self.feeder_load(f) for f in feeders]
        return {"feeders": feeders, "customers": sum(x["customers"] for x in loads),
                "critical_facilities": sum(x["critical_facilities"] for x in loads)}

    def parcel_exposure(self, parcel: str) -> dict:
        return self.exposure(self.feeders_of(parcel))

    def exposure_weight(self, parcel: str) -> int:
        e = self.parcel_exposure(parcel)
        return e["customers"] + FACILITY_WEIGHT * e["critical_facilities"]

    def node(self, node_id: str) -> dict | None:
        if node_id in self.parcels:
            return {"type": "parcel", "id": node_id, "assets": self.assets_of(node_id),
                    "feeders": self.feeders_of(node_id)}
        if node_id in self.assets:
            return {"type": "asset", "id": node_id, **self.assets[node_id]}
        if node_id in self.feeders:
            return {"type": "feeder", "id": node_id, "parcels": self.parcels_on(node_id),
                    "customer_groups": self.groups_on(node_id), **self.feeder_load(node_id)}
        if node_id in self.customer_groups:
            return {"type": "customer_group", "id": node_id, **self.customer_groups[node_id]}
        return None

    def to_doc(self) -> dict:
        return {"seed": self.seed, "synthetic": True, "parcels": list(self.parcels),
                "assets": self.assets, "feeders": list(self.feeders),
                "customer_groups": self.customer_groups, "mitigations": list(self.mitigations),
                "facility_weight": FACILITY_WEIGHT}

    @classmethod
    def from_doc(cls, doc: dict) -> "Graph":
        return cls(doc["seed"], tuple(doc["parcels"]), doc["assets"], tuple(doc["feeders"]),
                   doc["customer_groups"], tuple(doc["mitigations"]))


def build_graph(parcels, seed: int = GRAPH_SEED) -> Graph:
    rng = random.Random(seed)
    parcels = tuple(parcels)
    feeders = tuple(f"F{n:02d}" for n in range(1, FEEDER_COUNT + 1))
    assets, n = {}, 0
    for pid in parcels:
        for _ in range(rng.randint(1, 3)):
            n += 1
            assets[f"A{n:03d}"] = {"parcel": pid, "feeder": rng.choice(feeders),
                                   "type": rng.choice(ASSET_TYPES)}
    groups, g = {}, 0
    for feeder in feeders:
        for _ in range(rng.randint(2, 4)):
            g += 1
            facility = rng.choice(FACILITY_KINDS) if rng.random() < 0.3 else None
            groups[f"C{g:03d}"] = {"feeder": feeder, "customers": rng.randint(40, 900),
                                   "facility": facility}
    return Graph(seed, parcels, assets, feeders, groups, MITIGATIONS)


def write_graph(graph: Graph, path=DEFAULT_GRAPH_PATH) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(graph.to_doc(), sort_keys=True, indent=2) + "\n")


def read_graph(path=DEFAULT_GRAPH_PATH) -> Graph:
    return Graph.from_doc(json.loads(Path(path).read_text(encoding="utf-8")))
