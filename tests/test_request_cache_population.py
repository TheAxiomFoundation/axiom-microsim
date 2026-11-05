"""Changed population or engine inputs cannot reuse stale request bytes.

The engine and projection doubles leave each public runner's real request-cache
key construction in place. Reusing that key would reuse an encoded population
even when the result cache correctly requested a new engine baseline.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import orjson
import pytest

from axiom_microsim.run import microsim as M


@pytest.mark.parametrize("program", ["co-snap", "federal-ctc"])
@pytest.mark.parametrize("changed_part", ["path", "size", "mtime"])
def test_public_runner_request_cache_tracks_population_identity(
    program, changed_part, monkeypatch, tmp_path
):
    population = tmp_path / "population.h5"
    population.write_bytes(b"population")
    original_stat = population.stat()
    monkeypatch.delenv("AXIOM_ECPS_PATH", raising=False)
    monkeypatch.setenv("AXIOM_POPULACE_US_H5", str(population))
    batch = SimpleNamespace(
        state="CO",
        n_households=2,
        n_persons=3,
        household_weight=object(),
        tax_unit_weight=object(),
    )
    keys: list[tuple] = []

    def execute(projection, artifact_path, period_year, outputs, *, cache_key):
        keys.append(cache_key)
        return {}

    if program == "co-snap":
        monkeypatch.setattr(M, "project_co_snap", lambda *args, **kwargs: object())
        monkeypatch.setattr(M, "_artifact_for", lambda overrides: (tmp_path / "snap.json", None))
        monkeypatch.setattr(M, "_execute_compiled", execute)
        run = M.run_co_snap
    else:
        monkeypatch.setattr(
            M, "project_federal_ctc", lambda *args, **kwargs: SimpleNamespace(n_tax_units=2)
        )
        monkeypatch.setattr(M, "_ctc_artifact_for", lambda overrides: (tmp_path / "ctc.json", None))
        monkeypatch.setattr(M, "_execute_ctc", execute)
        run = M.run_federal_ctc

    run(batch)
    run(batch)
    assert keys[0] == keys[1], "identical population inputs lost the warm request-byte cache"

    if changed_part == "path":
        replacement = tmp_path / "other-population.h5"
        replacement.write_bytes(population.read_bytes())
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        monkeypatch.setenv("AXIOM_POPULACE_US_H5", str(replacement))
    elif changed_part == "size":
        population.write_bytes(b"a larger population")
        os.utime(population, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    else:
        os.utime(
            population,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000),
        )

    run(batch)
    assert keys[2] != keys[0], f"changed population {changed_part} reused encoded inputs"


@pytest.mark.parametrize("changed_part", ["path", "size", "mtime"])
def test_snap_bridge_recomputes_engine_derived_inputs_after_engine_replacement(
    changed_part, monkeypatch, tmp_path
):
    """An unchanged artifact can yield a new shelter deduction in a new engine."""
    engine = tmp_path / "engine"
    engine.write_bytes(b"first engine")
    original_stat = engine.stat()
    artifact = tmp_path / "snap.json"
    artifact.write_bytes(b"unchanged compiled artifact")
    monkeypatch.setattr(M, "ENGINE_BIN", engine)
    monkeypatch.setattr(M, "_CO_SNAP_REQUEST_CACHE", M._BoundedRequestCache())
    monkeypatch.setattr(
        M, "_co_snap_layout", lambda path: SimpleNamespace(slot_kinds=(M.HOUSEHOLD, M.PERSON))
    )
    projection = SimpleNamespace(n_households=1)
    deduction = 100
    shelter_runs = []

    def build_request(projection, year, output_ids, *, extra_household_inputs=None, layout):
        return {
            "dataset": {
                "inputs": [
                    {"name": name, "value": {"kind": "decimal", "value": str(values[0])}}
                    for name, values in (extra_household_inputs or {}).items()
                ]
            },
            "queries": [{"outputs": output_ids}],
        }

    def scalar_result(output, value):
        return SimpleNamespace(
            returncode=0,
            stdout=orjson.dumps(
                {
                    "results": [
                        {
                            "entity_id": "h0",
                            "outputs": {
                                output: {
                                    "kind": "scalar",
                                    "value": {"kind": "decimal", "value": str(value)},
                                }
                            },
                        }
                    ]
                }
            ),
        )

    def run_engine(cmd, *, input, capture_output):
        assert cmd[0] == str(M.ENGINE_BIN)
        assert capture_output
        request = orjson.loads(input)
        if request["queries"][0]["outputs"] == [M.CO_SNAP_EXCESS_SHELTER_OUTPUT]:
            shelter_runs.append(deduction)
            return scalar_result(M.CO_SNAP_EXCESS_SHELTER_OUTPUT, deduction)
        shelter = [
            record["value"]["value"]
            for record in request["dataset"]["inputs"]
            if record["name"] == M.FED_SNAP_EXCESS_SHELTER_INPUT
        ]
        if not shelter:
            return SimpleNamespace(
                returncode=1, stderr=b"missing input `snap_excess_shelter_deduction`"
            )
        return scalar_result(M.DEFAULT_OUTPUT_IDS["snap_allotment"], shelter[0])

    monkeypatch.setattr(M, "_build_compiled_request", build_request)
    monkeypatch.setattr(M.subprocess, "run", run_engine)

    def execute():
        return M._execute_compiled(
            projection,
            artifact,
            2026,
            ("snap_allotment",),
            cache_key=("co-snap", "CO", 2026, ("snap_allotment",), "fixed population"),
        )["snap_allotment"].tolist()

    assert execute() == execute() == [100]
    assert shelter_runs == [100], "identical engine inputs lost the warm shelter bridge"

    if changed_part == "path":
        replacement = tmp_path / "replacement-engine"
        replacement.write_bytes(engine.read_bytes())
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        monkeypatch.setattr(M, "ENGINE_BIN", replacement)
    elif changed_part == "size":
        engine.write_bytes(b"larger replacement engine")
        os.utime(engine, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    else:
        os.utime(engine, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000))
    deduction = 200

    assert execute() == [200], f"changed engine {changed_part} reused an old shelter deduction"
    assert execute() == [200]
    assert shelter_runs == [100, 200]
