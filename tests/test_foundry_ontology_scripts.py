import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_foundry_ontology_eval as ev  # noqa: E402


def test_is_throttle():
    assert ev.is_throttle(Exception("HTTP 429 Too Many Requests"))
    assert not ev.is_throttle(Exception("500 server error"))


def test_format_hint_requests_json_parcels():
    assert '"parcels"' in ev.FORMAT_HINT
