"""Compiled artifact fixtures for the tuple-order tests (see fixtures/artifacts/README.md)."""

from __future__ import annotations

import copy
import gzip
from pathlib import Path

import orjson

FIXTURES = Path(__file__).parent / "fixtures" / "artifacts"
# No slot_entities; count_related with current_slot 1 (pre-#179 legacy direction).
CTC_UNTYPED = FIXTURES / "federal-ctc.engine-v0.1.1.compiled.json"
# slot_entities [TaxUnit, Person]; count_related with current_slot 0 (post-#179).
CTC_TYPED = FIXTURES / "federal-ctc.engine-main-5a29e03.compiled.json"
# slot_entities [TaxUnit, Person] but current_slot 1: release v0.2.2, which has
# #140 (declared kinds carried) and not #179 (aggregation follows them).
CTC_TYPED_LEGACY_SLOTS = FIXTURES / "federal-ctc.engine-v0.2.2.compiled.json"
# Untyped member_of_household; Household count_related with current_slot 1.
CO_SNAP_UNTYPED = FIXTURES / "co-snap.engine-9106f44.compiled.json.gz"


def load_artifact(path: Path) -> dict:
    raw = path.read_bytes()
    return orjson.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def swap_aggregate_slots(artifact: dict) -> dict:
    """A copy with every aggregate's ``current_slot`` and ``related_slot`` swapped.

    A synthetic shape for exercising the request wiring, not engine output.
    """
    shaped = copy.deepcopy(artifact)

    def swap(node: object) -> None:
        if isinstance(node, dict):
            if node.get("kind") in ("count_related", "sum_related"):
                node["current_slot"], node["related_slot"] = (
                    node["related_slot"],
                    node["current_slot"],
                )
            for value in node.values():
                swap(value)
        elif isinstance(node, list):
            for value in node:
                swap(value)

    swap(shaped["program"])
    return shaped


def write_artifact(directory: Path, name: str, artifact: dict) -> Path:
    path = directory / name
    path.write_bytes(orjson.dumps(artifact))
    return path
