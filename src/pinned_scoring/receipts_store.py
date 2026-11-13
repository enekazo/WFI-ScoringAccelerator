"""File-backed receipt store: one JSON receipt per parcel, exposed through read-only tools.

Writing is a separate, explicit step (write_receipts). The reader class has no mutating
methods, verifies every receipt hash on load and returns deep copies so callers cannot
alter stored data.
"""

import copy
import json
import os
import re
import stat
from pathlib import Path

from .receipt import Receipt
from .scorer import score

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "results" / "receipts"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def build_receipts(observations_by_parcel: dict, policy, as_of) -> list:
    """Run the real scorer for every parcel."""
    return [score(pid, rows, policy, as_of) for pid, rows in observations_by_parcel.items()]


def write_receipts(receipts, directory=DEFAULT_DIR) -> list:
    """Write results/receipts/<parcel>.json and mark each file read-only on disk."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for receipt in receipts:
        if not _SAFE_ID.match(receipt.entity_id):
            raise ValueError(f"unsafe parcel id {receipt.entity_id!r}")
        path = directory / f"{receipt.entity_id}.json"
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.write_text(json.dumps(receipt.wire(), sort_keys=True, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
        os.chmod(path, stat.S_IREAD)
        paths.append(path)
    return paths


class ReadOnlyReceiptStore:
    """Read-only view over a receipts directory."""

    __slots__ = ("_dir",)

    def __init__(self, directory=DEFAULT_DIR):
        object.__setattr__(self, "_dir", Path(directory))

    def __setattr__(self, name, value):
        raise AttributeError("ReadOnlyReceiptStore is read-only")

    def get_receipt(self, parcel: str) -> dict:
        if not isinstance(parcel, str) or not _SAFE_ID.match(parcel):
            raise KeyError(f"invalid parcel id {parcel!r}")
        path = self._dir / f"{parcel}.json"
        if not path.is_file():
            raise KeyError(f"no receipt for parcel {parcel!r}")
        wire = json.loads(path.read_text(encoding="utf-8"))
        Receipt.from_wire(wire)  # raises ValueError if the file was altered
        return copy.deepcopy(wire)

    def list_receipts(self, band=None, status=None) -> list:
        """Summaries (parcel, status, score, band), optionally filtered by band and status."""
        out = []
        for path in sorted(self._dir.glob("*.json")):
            wire = self.get_receipt(path.stem)
            if band is not None and (wire["band"] or "").lower() != str(band).lower():
                continue
            if status is not None and wire["status"].lower() != str(status).lower():
                continue
            out.append({"parcel": wire["entity_id"], "status": wire["status"],
                        "score": wire["score"], "band": wire["band"]})
        return out


def get_receipt(parcel: str, directory=DEFAULT_DIR) -> dict:
    return ReadOnlyReceiptStore(directory).get_receipt(parcel)


def list_receipts(band=None, status=None, directory=DEFAULT_DIR) -> list:
    return ReadOnlyReceiptStore(directory).list_receipts(band, status)
