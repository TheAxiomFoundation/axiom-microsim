"""CO SNAP request-cache invariants (issue #26).

For fixed population inputs, outputs and relation layout:
* Artifacts with different shelter values at encoded precision cannot share a bridge.
* An artifact and period yield identical bytes, including warm hits and copied files.
* Cached execution sends the same request as uncached execution/bridge construction.
* Plain inputs remain shared across artifacts; a warm bridge needs one engine call.

Generated shelter amounts are integral and exactly representable by the request's
six-decimal encoding; sub-precision differences cannot imply different bytes.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from axiom_microsim.data.ecps_loader import EcpsBatch
from axiom_microsim.run import microsim as M

OUTPUTS = ("snap_allotment",)


def _batch(sizes: list[int]) -> EcpsBatch:
    n = sum(sizes)
    return EcpsBatch(
        state="CO",
        year="2024",
        n_persons=n,
        n_households=len(sizes),
        person_household_index=np.repeat(np.arange(len(sizes)), sizes),
        household_weight=np.ones(len(sizes)),
        person_columns={
            "age": np.full(n, 30),
            "is_disabled": np.zeros(n, dtype=bool),
            "employment_income_before_lsr": np.zeros(n),
            "self_employment_income_before_lsr": np.zeros(n),
            "pre_subsidy_rent": np.full(n, 12_000),
        },
    )


def _artifact(path: Path, shelter: list[int], *, bridge: bool = True, owner_slot: int = 1):
    """Minimal compiled-artifact shape; the subprocess double returns these outputs.

    Doubles stand in for compilation and the engine process, and ``_runner`` swaps
    in small household and person schemas. Projection, relation layout inference,
    bridge collection, request encoding and caching run for real.
    """
    kinds = [M.PERSON, M.PERSON]
    kinds[owner_slot] = M.HOUSEHOLD
    artifact = {
        "program": {
            "relations": [{"name": M.CO_SNAP_RELATION_NAME, "arity": 2, "slot_entities": kinds}],
        },
        "test_shelter_outputs": shelter,
        "test_needs_bridge": bridge,
    }
    path.write_bytes(orjson.dumps(artifact))
    return path


@contextmanager
def _runner(sizes: list[int]):
    # Each Hypothesis example gets its own artifacts, subprocess double and LRU.
    with (
        TemporaryDirectory(prefix="co-snap-cache-") as directory,
        pytest.MonkeyPatch.context() as mp,
    ):
        root = Path(directory)
        batch = _batch(sizes)
        calls: list[tuple[Path, bytes]] = []

        def fake_run(cmd, *, input, capture_output):
            assert capture_output
            path = Path(cmd[-1])
            artifact = orjson.loads(path.read_bytes())
            request = orjson.loads(input)
            calls.append((path, input))
            queries = request["queries"]
            if queries[0]["outputs"] == [M.CO_SNAP_EXCESS_SHELTER_OUTPUT]:
                results = [
                    {
                        "entity_id": f"h{i}",
                        "outputs": {
                            M.CO_SNAP_EXCESS_SHELTER_OUTPUT: {
                                "kind": "scalar",
                                "value": {"kind": "decimal", "value": str(value)},
                            }
                        },
                    }
                    for i, value in enumerate(artifact["test_shelter_outputs"])
                ]
                return SimpleNamespace(returncode=0, stdout=orjson.dumps({"results": results}))
            has_shelter = any(
                record["name"] == M.FED_SNAP_EXCESS_SHELTER_INPUT
                for record in request["dataset"]["inputs"]
            )
            if artifact["test_needs_bridge"] and not has_shelter:
                return SimpleNamespace(
                    returncode=1, stderr=b"missing input `snap_excess_shelter_deduction`"
                )
            return SimpleNamespace(returncode=0, stdout=b'{"results": []}')

        mp.setattr(M, "_CO_SNAP_REQUEST_CACHE", M._BoundedRequestCache())
        mp.setattr(M, "_HH_SLOTS", [M._SlotSpec("household_size", "integer", 1)])
        mp.setattr(M, "_PERSON_SLOTS", [M._SlotSpec("member_age", "integer", 0)])
        mp.setattr(M.subprocess, "run", fake_run)
        yield SimpleNamespace(root=root, batch=batch, calls=calls, patch=mp)


def _shelter_values(request_bytes: bytes) -> list[float]:
    return [
        float(record["value"]["value"])
        for record in orjson.loads(request_bytes)["dataset"]["inputs"]
        if record["name"] == M.FED_SNAP_EXCESS_SHELTER_INPUT
    ]


def test_baseline_then_shelter_reform_uses_different_bridged_inputs():
    with _runner([2, 1]) as runner:
        baseline = _artifact(runner.root / "baseline.json", [100, 200])
        reform = _artifact(runner.root / "reform.json", [400, 500])
        override = M.ParameterOverride(
            repo="rules-us-co",
            file_relative="regulations/10-ccr-2506-1/4.407.3.yaml",
            parameter="excess_shelter_deduction",
            patch_kind="set_formula",
            formula="400",
        )

        def artifact_for(overrides):
            if overrides:
                assert overrides == [override]
                return reform, None
            return baseline, None

        runner.patch.setattr(M, "_artifact_for", artifact_for)
        M.run_co_snap(runner.batch, outputs=OUTPUTS)
        baseline_request = runner.calls[-1][1]
        M.run_co_snap(runner.batch, overrides=[override], outputs=OUTPUTS)
        reform_request = runner.calls[-1][1]

        assert _shelter_values(baseline_request) == [100, 200]
        assert _shelter_values(reform_request) == [400, 500]
        assert baseline_request != reform_request


@st.composite
def shelter_cases(draw):
    sizes = draw(st.lists(st.integers(1, 4), min_size=1, max_size=4))
    shelter = draw(st.lists(st.integers(0, 10_000), min_size=len(sizes), max_size=len(sizes)))
    other = shelter.copy()
    index = draw(st.integers(0, len(sizes) - 1))
    other[index] += draw(st.integers(1, 10_000))
    return SimpleNamespace(
        sizes=sizes,
        shelter=shelter,
        other=other,
        year=draw(st.integers(2020, 2035)),
        owner_slot=draw(st.integers(0, 1)),
        outputs=draw(st.sampled_from([OUTPUTS, M.DEFAULT_OUTPUTS])),
    )


def _execute(runner, artifact: Path, year: int, outputs=OUTPUTS, *, cached=True) -> bytes:
    projection = M.project_co_snap(runner.batch, period_year=year)
    key = ("co-snap", runner.batch.state, year, outputs) if cached else None
    M._execute_compiled(projection, artifact, year, outputs, cache_key=key)
    return runner.calls[-1][1]


@pytest.mark.parametrize("reuse_path", [False, True], ids=["different-paths", "same-path"])
@settings(max_examples=40, deadline=None)
@given(case=shelter_cases())
def test_different_shelter_artifacts_never_share_bridged_bytes(case, reuse_path):
    with _runner(case.sizes) as runner:
        path = _artifact(runner.root / "first.json", case.shelter, owner_slot=case.owner_slot)
        first = _execute(runner, path, case.year, case.outputs)
        second_path = path if reuse_path else runner.root / "second.json"
        _artifact(second_path, case.other, owner_slot=case.owner_slot)
        second = _execute(runner, second_path, case.year, case.outputs)

        assert _shelter_values(first) == case.shelter
        assert _shelter_values(second) == case.other
        assert first != second


@settings(max_examples=40, deadline=None)
@given(case=shelter_cases())
def test_same_artifact_and_period_are_byte_identical_and_warm_run_is_cheap(case):
    with _runner(case.sizes) as runner:
        path = _artifact(runner.root / "baseline.json", case.shelter, owner_slot=case.owner_slot)
        first = _execute(runner, path, case.year, case.outputs)
        assert len(runner.calls) == 3  # Missing input, shelter computation, retry.
        assert _shelter_values(first) == case.shelter

        copy = runner.root / "copied.json"
        copy.write_bytes(path.read_bytes())
        for artifact in (path, copy):
            before = len(runner.calls)
            repeated = _execute(runner, artifact, case.year, case.outputs)
            assert repeated == first
            assert len(runner.calls) == before + 1


@settings(max_examples=40, deadline=None)
@given(case=shelter_cases())
def test_cached_execution_matches_uncached_execution_and_bridge_builder(case):
    with _runner(case.sizes) as runner:
        paths = [
            _artifact(runner.root / "baseline.json", case.shelter, owner_slot=case.owner_slot),
            _artifact(runner.root / "reform.json", case.other, owner_slot=case.owner_slot),
        ]
        # Warm both artifacts in both periods before comparing; this exposes
        # collisions between artifacts or periods regardless of run order.
        for year in (case.year, case.year + 1):
            for path in paths:
                _execute(runner, path, year, case.outputs)
        for year in (case.year, case.year + 1):
            for path in reversed(paths):
                cached = _execute(runner, path, year, case.outputs)
                uncached = _execute(runner, path, year, case.outputs, cached=False)
                built = M._build_co_snap_shelter_bridge_request_bytes(
                    M.project_co_snap(runner.batch, period_year=year),
                    path,
                    year,
                    [M.DEFAULT_OUTPUT_IDS[name] for name in case.outputs],
                    layout=M._co_snap_layout(path),
                )
                assert cached == uncached == built


@settings(max_examples=40, deadline=None)
@given(case=shelter_cases())
def test_plain_request_is_built_once_and_shared_across_artifacts(case):
    with _runner(case.sizes) as runner:
        builds = []
        build = M._build_compiled_request

        def counted_build(*args, **kwargs):
            builds.append(None)
            return build(*args, **kwargs)

        runner.patch.setattr(M, "_build_compiled_request", counted_build)
        first = _artifact(
            runner.root / "baseline.json", case.shelter, bridge=False, owner_slot=case.owner_slot
        )
        second = _artifact(
            runner.root / "reform.json", case.other, bridge=False, owner_slot=case.owner_slot
        )
        baseline = _execute(runner, first, case.year, case.outputs)
        reform = _execute(runner, second, case.year, case.outputs)

        assert baseline == reform
        assert _shelter_values(baseline) == []
        assert len(builds) == 1
        assert len(runner.calls) == 2
