"""Changed population, batch or engine inputs cannot reuse stale request bytes.

The engine and projection doubles leave each public runner's real request-cache
key construction in place. Reusing that key would reuse an encoded population
even when the result cache correctly requested a new engine baseline.

For every batch of either type, equal contents digest alike and a change to any
element of any field digests differently, so a caller's own batch never reads
another batch's encoded request.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from types import SimpleNamespace

import numpy as np
import orjson
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _artifacts import CO_SNAP_UNTYPED, CTC_UNTYPED, load_artifact, write_artifact
from axiom_microsim.data.ecps_loader import EcpsBatch, TaxUnitBatch
from axiom_microsim.run import microsim as M


def _snap_batch(ages: list[int]) -> EcpsBatch:
    n = len(ages)
    return EcpsBatch(
        state="CO",
        year="2024",
        n_persons=n,
        n_households=1,
        person_household_index=np.zeros(n, dtype=np.int64),
        household_weight=np.ones(1),
        person_columns={
            "age": np.asarray(ages, dtype=np.int64),
            "is_disabled": np.zeros(n, dtype=bool),
            "employment_income_before_lsr": np.zeros(n),
            "self_employment_income_before_lsr": np.zeros(n),
            "pre_subsidy_rent": np.full(n, 12_000),
        },
    )


def _ctc_batch(ages: list[int]) -> TaxUnitBatch:
    n = len(ages)
    return TaxUnitBatch(
        state="CO",
        year="2024",
        n_persons=n,
        n_tax_units=1,
        person_tax_unit_index=np.zeros(n, dtype=np.int64),
        tax_unit_weight=np.ones(1),
        person_columns={"age": np.asarray(ages, dtype=np.int64)},
    )


@pytest.mark.parametrize("program", ["co-snap", "federal-ctc"])
def test_same_shaped_batches_never_share_an_encoded_request(program, monkeypatch, tmp_path):
    """Projection, encoding and caching run for real; only the engine process is a double."""
    sent: list[bytes] = []

    def run_engine(cmd, *, input, capture_output):
        sent.append(input)
        return SimpleNamespace(returncode=0, stdout=b'{"results": []}', stderr=b"")

    monkeypatch.setattr(M.subprocess, "run", run_engine)
    monkeypatch.setattr(M, "population_identity", lambda: ("fixed population",))
    if program == "co-snap":
        cache = M._BoundedRequestCache()
        monkeypatch.setattr(M, "_CO_SNAP_REQUEST_CACHE", cache)
        monkeypatch.setattr(M, "_HH_SLOTS", [M._SlotSpec("household_size", "integer", 1)])
        monkeypatch.setattr(M, "_PERSON_SLOTS", [M._SlotSpec("member_age", "integer", 0)])
        artifact = write_artifact(tmp_path, "co-snap.json", load_artifact(CO_SNAP_UNTYPED))
        monkeypatch.setattr(M, "_artifact_for", lambda overrides: (artifact, None))
        run, batch = M.run_co_snap, _snap_batch
    else:
        cache = M._BoundedRequestCache()
        monkeypatch.setattr(M, "_CTC_REQUEST_CACHE", cache)
        monkeypatch.setattr(M, "_ctc_artifact_for", lambda overrides: (CTC_UNTYPED, None))
        run, batch = M.run_federal_ctc, _ctc_batch

    run(batch([40, 10]))
    run(batch([40, 30]))
    run(batch([40, 10]))

    first, other, repeat = sent
    assert first != other, "a second batch of the same shape read the first batch's request"
    assert repeat == first
    assert len(cache) == 2, "an equal batch rebuilt its encoded request"


def _ints(n: int) -> st.SearchStrategy[np.ndarray]:
    return st.lists(st.integers(0, 1_000), min_size=n, max_size=n).map(
        lambda values: np.asarray(values, dtype=np.int64)
    )


def _floats(n: int) -> st.SearchStrategy[np.ndarray]:
    return _ints(n).map(lambda values: values.astype(np.float64))


@st.composite
def _batches(draw: st.DrawFn) -> EcpsBatch | TaxUnitBatch:
    n_persons = draw(st.integers(1, 5))
    n_groups = draw(st.integers(1, 3))
    state = draw(st.sampled_from(["CO", "US"]))
    person_columns = {"age": draw(_ints(n_persons)), "income": draw(_floats(n_persons))}
    index = draw(_ints(n_persons))
    weight = draw(_floats(n_groups))
    if draw(st.booleans()):
        return EcpsBatch(
            state=state,
            year="2024",
            n_persons=n_persons,
            n_households=n_groups,
            person_household_index=index,
            household_weight=weight,
            person_columns=person_columns,
            household_columns={"rent": draw(_floats(n_groups))},
        )
    return TaxUnitBatch(
        state=state,
        year="2024",
        n_persons=n_persons,
        n_tax_units=n_groups,
        person_tax_unit_index=index,
        tax_unit_weight=weight,
        person_columns=person_columns,
    )


def _copies_with_one_change(
    batch: EcpsBatch | TaxUnitBatch, element: int
) -> Iterator[tuple[str, EcpsBatch | TaxUnitBatch]]:
    """Yield one altered copy per field, and per column of a field holding columns."""
    for name, value in vars(batch).items():
        changed = copy.deepcopy(batch)
        if isinstance(value, dict):
            for column in value:
                changed = copy.deepcopy(batch)
                array = getattr(changed, name)[column]
                array[element % len(array)] += 1
                yield f"{name}.{column}", changed
        elif isinstance(value, np.ndarray):
            array = getattr(changed, name)
            array[element % len(array)] += 1
            yield name, changed
        else:
            setattr(changed, name, value + ("x" if isinstance(value, str) else 1))
            yield name, changed


@settings(max_examples=100, deadline=None)
@given(batch=_batches(), element=st.integers(0, 100))
def test_batch_digest_follows_contents_alone(batch, element):
    digest = M._batch_digest(batch)
    assert M._batch_digest(copy.deepcopy(batch)) == digest
    for changed_field, changed in _copies_with_one_change(batch, element):
        assert M._batch_digest(changed) != digest, f"the digest ignored {changed_field}"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(np.zeros(2, dtype=np.int64), np.zeros(2, dtype=np.float64), id="dtype"),
        pytest.param(np.zeros(4), np.zeros((2, 2)), id="shape"),
        pytest.param(
            np.array(["ab", "c"], dtype=object),
            np.array(["a", "bc"], dtype=object),
            id="object-values",
        ),
    ],
)
def test_batch_digest_separates_columns_whose_bytes_could_match(first, second):
    assert M._batch_digest(SimpleNamespace(column=first)) != M._batch_digest(
        SimpleNamespace(column=second)
    )
    assert M._batch_digest(SimpleNamespace(columns={"x": first})) != M._batch_digest(
        SimpleNamespace(columns={"x": second})
    )


def test_batch_digest_reads_object_columns_by_value():
    """Separately built strings hold different pointers and the same text."""

    def column() -> np.ndarray:
        return np.array(["".join(["a", "b"]), "".join(["c", "d"])], dtype=object)

    assert M._batch_digest(SimpleNamespace(column=column())) == M._batch_digest(
        SimpleNamespace(column=column())
    )


def test_batch_digest_reads_names_and_ignores_their_order():
    age, income = np.array([40, 10]), np.array([1.5, 2.5])
    digest = M._batch_digest(SimpleNamespace(state="CO", columns={"age": age, "income": income}))

    assert digest == M._batch_digest(
        SimpleNamespace(columns={"income": income, "age": age}, state="CO")
    )
    assert M._batch_digest(SimpleNamespace(columns={"age": age})) != M._batch_digest(
        SimpleNamespace(columns={"years": age})
    )
    assert M._batch_digest(SimpleNamespace(left=age)) != M._batch_digest(SimpleNamespace(right=age))


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
