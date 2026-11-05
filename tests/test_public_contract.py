"""Public overrides are a finite numeric policy contract, validated before work.

The local ParameterOverride API deliberately remains more expressive. These tests
exercise the public models and ASGI routes, including requests rejected before
scratch creation, patching, compilation, or the PolicyEngine subprocess.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
from pydantic import ValidationError

from axiom_microsim import public_contract as contract
from axiom_microsim import server
from axiom_microsim.run import microsim as runtime

SNAP_FILE = "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml"
SNAP_TABLE = "snap_maximum_allotment_table"
CTC_FILE = "statutes/26/24/h.yaml"
TAX_FILE = "policies/irs/rev-proc-2025-32/income-tax-brackets.yaml"
VALID = {
    "repo": "rules-us",
    "file_relative": SNAP_FILE,
    "parameter": SNAP_TABLE,
    "patch_kind": "scale_values",
    "multiplier": 1.0,
}
PE_VALID = {"path": "gov.irs.credits.ctc.amount.base[0].amount", "value": 2200}


@pytest.fixture
def before_work(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every refusal must occur before entering even a program runner."""
    calls: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        calls.append("scratch/patch/compile/run")
        raise HTTPException(500, "public validation allowed expensive work to start")

    for name in ("_patch_yaml", "_compile"):
        monkeypatch.setattr(runtime, name, forbidden)
    monkeypatch.setattr(runtime.shutil, "copytree", forbidden)
    monkeypatch.setattr(runtime.tempfile, "mkdtemp", forbidden)
    for name in ("_run_co_snap", "_run_federal_income_tax", "_run_federal_ctc"):
        monkeypatch.setattr(server, name, forbidden)
    monkeypatch.setattr(server._subprocess, "run", forbidden)
    return calls


def _refuse(
    before_work: list[str],
    override: dict[str, Any],
    field: str,
    *,
    route: str = "/microsim",
    program: str | None = None,
) -> None:
    response = TestClient(server.app, raise_server_exceptions=False).post(
        route,
        json={
            "program": program or ("co-snap" if route == "/microsim" else "federal-ctc"),
            "overrides": [override],
        },
    )
    assert 400 <= response.status_code < 500, response.text
    assert field in response.text, response.text
    assert not before_work


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("file_relative", "unknown.yaml", id="unknown-file"),
        pytest.param("parameter", "not_a_policy_lever", id="unknown-parameter"),
        pytest.param("repo", "rules-us-co", id="unknown-repo"),
        pytest.param("file_relative", "/etc/passwd", id="absolute-path"),
        pytest.param("file_relative", f"../{SNAP_FILE}", id="parent-path"),
        pytest.param("file_relative", f"./{SNAP_FILE}", id="normalized-alias"),
    ],
)
def test_refuses_non_allowlisted_targets(before_work: list[str], field: str, value: str) -> None:
    _refuse(before_work, {**VALID, field: value}, field)


@pytest.mark.parametrize(
    "kind", ["set_formula", "set_values", "scale_formula", "set_scalar", "scale_scalar"]
)
def test_refuses_disallowed_patch_kind(before_work: list[str], kind: str) -> None:
    _refuse(before_work, {**VALID, "patch_kind": kind}, "patch_kind")


@pytest.mark.parametrize("multiplier", [0.499, 2.001, -1, 1e200])
def test_refuses_out_of_range_multiplier(before_work: list[str], multiplier: float) -> None:
    _refuse(before_work, {**VALID, "multiplier": multiplier}, "multiplier")


@pytest.mark.parametrize(
    "override",
    [
        {
            "repo": "rules-us",
            "file_relative": CTC_FILE,
            "parameter": "ctc_child_amount_under_subsection_h",
            "patch_kind": "set_scalar",
            "value": 6001,
        },
    ],
    ids=["scalar-bounds"],
)
def test_refuses_out_of_range_values(before_work: list[str], override: dict[str, Any]) -> None:
    _refuse(before_work, override, "value")


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
@pytest.mark.parametrize("year", [2018, 2025, 2027, 0, -1, "2026", 2026.5, None])
def test_refuses_every_year_but_2026(before_work: list[str], route: str, year: Any) -> None:
    response = TestClient(server.app, raise_server_exceptions=False).post(
        route, json={"program": "co-snap", "state": "CO", "year": year}
    )
    assert response.status_code == 422, response.text
    assert "year" in response.text, response.text
    assert not before_work


@pytest.mark.parametrize("model", [server.MicrosimRequest, server.CompareRequest])
def test_public_routes_default_to_the_year_the_web_sends(model: Any) -> None:
    assert model.model_validate({"program": "co-snap", "state": "CO"}).year == 2026


@settings(max_examples=100, deadline=None)
@given(
    model=st.sampled_from([server.MicrosimRequest, server.CompareRequest]),
    year=st.integers(min_value=-(10**6), max_value=10**6),
)
@example(model=server.MicrosimRequest, year=2026)
@example(model=server.CompareRequest, year=2026)
def test_invariant_public_routes_accept_only_2026(model: Any, year: int) -> None:
    try:
        request = model.model_validate({"program": "co-snap", "state": "CO", "year": year})
    except ValidationError:
        assert year != 2026
    else:
        assert year == 2026 and request.year == 2026


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_refuses_formula_field(before_work: list[str], route: str) -> None:
    override = VALID if route == "/microsim" else PE_VALID
    _refuse(before_work, {**override, "formula": "caller_supplied_code()"}, "formula", route=route)


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_refuses_too_many_overrides(before_work: list[str], route: str) -> None:
    override = VALID if route == "/microsim" else PE_VALID
    response = TestClient(server.app).post(
        route,
        json={
            "program": "co-snap" if route == "/microsim" else "federal-ctc",
            "overrides": [override] * (contract.MAX_OVERRIDES + 1),
        },
    )
    assert response.status_code == 422, response.text
    assert "overrides" in response.text
    assert str(contract.MAX_OVERRIDES) in response.text, "the count limit must reject first"
    assert not before_work


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
@pytest.mark.parametrize("length_header", [None, "1"])
def test_refuses_oversized_streamed_body(
    before_work: list[str], route: str, length_header: str | None
) -> None:
    body = json.dumps({"program": "co-snap", "state": "x" * contract.MAX_BODY_BYTES}).encode()
    headers = {"content-type": "application/json"}
    if length_header is not None:
        headers["content-length"] = length_header
    # Distinct ASGI receive messages prove aggregate counting: HTTP test clients
    # may silently combine an iterable's chunks into one receive message.
    chunks = [body[:100], body[100:]]
    assert all(len(chunk) < contract.MAX_BODY_BYTES for chunk in chunks)
    sent: list[dict[str, Any]] = []

    async def invoke() -> None:
        messages = iter(
            {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)
        )

        async def receive() -> dict[str, Any]:
            return next(messages, {"type": "http.disconnect"})

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await server.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": route,
                "raw_path": route.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                "client": ("127.0.0.1", 1),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    assert (
        next(message["status"] for message in sent if message["type"] == "http.response.start")
        == 413
    )
    assert b"body" in b"".join(message.get("body", b"") for message in sent).lower()
    assert not before_work


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_refuses_duplicate_targets(before_work: list[str], route: str) -> None:
    override = VALID if route == "/microsim" else PE_VALID
    response = TestClient(server.app).post(
        route,
        json={
            "program": "co-snap" if route == "/microsim" else "federal-ctc",
            "overrides": [override, override],
        },
    )
    assert response.status_code == 422, response.text
    assert "duplicate" in response.text.lower()
    assert not before_work


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_refuses_override_for_wrong_program(before_work: list[str], route: str) -> None:
    _refuse(
        before_work,
        VALID if route == "/microsim" else PE_VALID,
        "program",
        route=route,
        program="federal-income-tax",
    )


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"path": "gov.anything", "value": 1}, "path"),
        ({**PE_VALID, "value": 6001}, "value"),
        ({"path": "gov.irs.income.bracket.rates.1", "value": 0.151}, "value"),
    ],
)
def test_compare_refuses_unknown_or_unbounded_levers(
    before_work: list[str], override: dict[str, Any], field: str
) -> None:
    _refuse(before_work, override, field, route="/compare")


@pytest.mark.parametrize("value", [True, "1", float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("model", [server.OverrideIn, server.PeOverrideIn])
def test_refuses_non_numeric_or_nonfinite_payload(value: Any, model: Any) -> None:
    payload = (
        {**VALID, "multiplier": value}
        if model is server.OverrideIn
        else {**PE_VALID, "value": value}
    )
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("literal", ["1e309", "NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("position", ["multiplier", "formula-scale", "scalar", "pe-value"])
def test_refuses_nonfinite_json_before_work(
    before_work: list[str], literal: str, position: str
) -> None:
    override: dict[str, Any]
    route = "/microsim"
    program = "co-snap"
    if position == "pe-value":
        route, program = "/compare", "federal-ctc"
        override = {**PE_VALID, "value": "NONFINITE"}
    elif position == "scalar":
        program = "federal-ctc"
        override = {
            "repo": "rules-us",
            "file_relative": CTC_FILE,
            "parameter": "ctc_child_amount_under_subsection_h",
            "patch_kind": "set_scalar",
            "value": "NONFINITE",
        }
    elif position == "formula-scale":
        override = {
            **VALID,
            "parameter": "snap_maximum_allotment_additional_member",
            "patch_kind": "scale_formula",
            "multiplier": "NONFINITE",
        }
    else:
        override = {**VALID, "multiplier": "NONFINITE"}
    body = json.dumps({"program": program, "overrides": [override]}).replace('"NONFINITE"', literal)
    response = TestClient(server.app, raise_server_exceptions=False).post(
        route, content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert not before_work


@pytest.mark.parametrize("route", ["/microsim", "/compare"])
def test_refuses_unencodable_extra_field_before_work(before_work: list[str], route: str) -> None:
    response = TestClient(server.app, raise_server_exceptions=False).post(
        route,
        content=json.dumps({"program": "co-snap", "\ud800": "unknown field"}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    # Pydantic may reject invalid Unicode for the whole body before it can
    # attach a location to the individual extra key.
    assert any(error["loc"][0] == "body" for error in response.json()["detail"])
    assert not before_work


@pytest.mark.parametrize("value", [0, 1e-100, 2e-17, 0.1, 6000])
def test_scalar_runtime_conversion_preserves_numeric_literal(value: float) -> None:
    override = server.OverrideIn.model_validate(
        {
            "repo": "rules-us",
            "file_relative": CTC_FILE,
            "parameter": "ctc_child_amount_under_subsection_h",
            "patch_kind": "set_scalar",
            "value": value,
        }
    ).to_runtime()
    assert override.patch_kind == "set_formula"
    assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", override.formula)
    assert float(override.formula) == value


@pytest.mark.parametrize("multiplier", [0.5, 1, 1.01, 2])
def test_scale_formula_runtime_conversion_preserves_multiplier(multiplier: float) -> None:
    override = server.OverrideIn.model_validate(
        {
            **VALID,
            "parameter": "snap_maximum_allotment_additional_member",
            "patch_kind": "scale_formula",
            "multiplier": multiplier,
        }
    ).to_runtime()
    assert override.patch_kind == "scale_formula"
    assert override.multiplier == multiplier
    assert override.formula is None


def test_zero_table_scale_runtime_conversion_preserves_multiplier() -> None:
    override = server.OverrideIn.model_validate(
        {
            **VALID,
            "file_relative": "policies/usda/snap/fy-2026-cola/deductions.yaml",
            "parameter": "snap_standard_deduction_48_states_dc_table",
            "multiplier": 0,
        }
    ).to_runtime()
    assert override.patch_kind == "scale_values"
    assert override.multiplier == 0
    assert override.values is None


def test_public_schema_has_only_catalogued_kinds_and_no_formula_or_values_fields() -> None:
    schema = server.OverrideIn.model_json_schema()
    assert "formula" not in schema["properties"]
    assert "values" not in schema["properties"]
    assert set(schema["properties"]["patch_kind"]["enum"]) == {
        "scale_values",
        "scale_formula",
        "set_scalar",
    }
    assert "formula" not in server.PeOverrideIn.model_json_schema()["properties"]


@pytest.mark.parametrize(
    "change",
    [
        {"multiplier": None},
        {"values": {1: 300}},
        {"value": 300},
        {"unexpected": "ignored fields must not enter the public contract"},
    ],
)
def test_refuses_mismatched_or_extra_payload(
    change: dict[str, Any], before_work: list[str]
) -> None:
    _refuse(before_work, {**VALID, **change}, next(iter(change)))


def _frontend_extremes() -> list[dict[str, Any]]:
    """Execute actual frontend builders; erase declarations for CI's Node version.

    This erases the few TypeScript-only declarations in this source file without
    installing npm packages into the Python test environment. All lever objects,
    numeric bounds, build functions, and peBuild functions execute unchanged.
    """
    source = (Path(__file__).parents[1] / "web/src/lib/levers.ts").read_text()
    source = source.split("export function programById", 1)[0]
    source = re.sub(r"^import type .*?;\s*", "", source, flags=re.MULTILINE)
    source = re.sub(r"export type ProgramId = .*?;\s*", "", source)
    source = re.sub(r"export interface \w+ \{.*?^\}\s*", "", source, flags=re.DOTALL | re.MULTILINE)
    source = re.sub(r"(: Program\[\]|: Program)(?=\s*=)", "", source)
    source = source.replace("export const PROGRAMS", "const PROGRAMS")
    source += """
console.log(JSON.stringify(PROGRAMS.flatMap(p => p.levers.flatMap(l => [l.min, l.max].map(v => ({
  program: p.id, state: p.default_state, id: l.id, value: v,
  overrides: l.build(v), pe: l.peBuild?.(v) ?? []
}))))));
"""
    output = subprocess.run(
        [
            "env",
            "-u",
            "ANTHROPIC_API_KEY",
            "-u",
            "OPENAI_API_KEY",
            "-u",
            "CODEX_API_KEY",
            "node",
            "--input-type=module",
            "-e",
            source,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(output.stdout)


def test_every_frontend_lever_accepts_both_extremes() -> None:
    extremes = _frontend_extremes()
    assert len(extremes) == 16, "update this check when a frontend lever is added"
    combined: dict[str, list[dict[str, Any]]] = {}
    for case in extremes:
        request = server.MicrosimRequest.model_validate(
            {
                "program": case["program"],
                "state": case["state"],
                "overrides": case["overrides"],
            }
        )
        assert len(request.overrides) == len(case["overrides"]), case
        server.CompareRequest.model_validate(
            {"program": case["program"], "state": case["state"], "overrides": case["pe"]}
        )
        # Every slider can be changed simultaneously in the UI.
        combined.setdefault(case["program"], []).append(case)
    for program, cases in combined.items():
        for parity in (0, 1):
            selected = cases[parity::2]
            server.MicrosimRequest.model_validate(
                {
                    "program": program,
                    "state": selected[0]["state"],
                    "overrides": [o for c in selected for o in c["overrides"]],
                }
            )
            server.CompareRequest.model_validate(
                {
                    "program": program,
                    "state": selected[0]["state"],
                    "overrides": [o for c in selected for o in c["pe"]],
                }
            )


_target_strings = st.one_of(st.sampled_from([SNAP_FILE, TAX_FILE, CTC_FILE]), st.text())
_parameter_strings = st.one_of(
    st.sampled_from(
        [SNAP_TABLE, "income_tax_bracket_rates", "ctc_child_amount_under_subsection_h"]
    ),
    st.text(),
)
_candidate = st.fixed_dictionaries(
    {"file_relative": _target_strings, "parameter": _parameter_strings},
).map(lambda fields: {**VALID, **fields})


@st.composite
def _catalogued_override(draw: st.DrawFn) -> dict[str, Any]:
    target, policy = draw(st.sampled_from(list(contract.ALLOWED_TARGETS.items())))
    kind = draw(st.sampled_from(policy.kinds))
    return {
        "repo": target[0],
        "file_relative": target[1],
        "parameter": target[2],
        "patch_kind": kind,
        "value" if kind == "set_scalar" else "multiplier": draw(
            st.floats(min_value=policy.minimum, max_value=policy.maximum)
        ),
    }


_candidate = st.one_of(
    _candidate,
    _catalogued_override(),
    _catalogued_override().flatmap(
        lambda override: st.text().map(lambda kind: {**override, "patch_kind": kind})
    ),
)
_pe_candidate = st.fixed_dictionaries(
    {
        "path": st.one_of(st.just(PE_VALID["path"]), st.text()),
        "value": st.one_of(st.just(2200), st.floats(allow_nan=False, allow_infinity=False)),
    }
)


@settings(max_examples=100, deadline=None)
@given(
    overrides=st.lists(_candidate, max_size=18),
    pe_overrides=st.lists(_pe_candidate, max_size=18),
    program=st.sampled_from(["co-snap", "federal-income-tax", "federal-ctc"]),
)
@example(overrides=[VALID], pe_overrides=[], program="co-snap")
@example(overrides=[{**VALID, "patch_kind": "set_values"}], pe_overrides=[], program="co-snap")
@example(overrides=[], pe_overrides=[PE_VALID], program="federal-ctc")
def test_invariant_accepted_requests_only_name_allowlisted_targets(
    overrides: list[dict[str, Any]], pe_overrides: list[dict[str, Any]], program: str
) -> None:
    try:
        request = server.MicrosimRequest.model_validate(
            {"program": program, "state": "CO", "overrides": overrides}
        )
    except ValidationError:
        pass
    else:
        assert all(
            o.target in contract.ALLOWED_TARGETS
            and o.patch_kind in contract.ALLOWED_TARGETS[o.target].kinds
            and o.patch_kind in {"scale_values", "scale_formula", "set_scalar"}
            and contract.ALLOWED_TARGETS[o.target].program == program
            for o in request.overrides
        )
    try:
        pe_request = server.CompareRequest.model_validate(
            {"program": program, "state": "CO", "overrides": pe_overrides}
        )
    except ValidationError:
        pass
    else:
        assert all(
            o.path in contract.PE_ALLOWED_PATHS and contract.PE_ALLOWED_PATHS[o.path][0] == program
            for o in pe_request.overrides
        )


@settings(max_examples=50, deadline=None)
@given(override=_catalogued_override(), formula=st.one_of(st.none(), st.text()))
def test_invariant_no_public_kind_accepts_caller_formula(
    override: dict[str, Any], formula: str | None
) -> None:
    assert server.OverrideIn.model_validate(override)
    with pytest.raises(ValidationError, match="formula"):
        server.OverrideIn.model_validate({**override, "formula": formula})
    with pytest.raises(ValidationError, match="formula"):
        server.PeOverrideIn.model_validate({**PE_VALID, "formula": formula})


@settings(max_examples=50, deadline=None)
@given(override=_catalogued_override())
def test_invariant_catalogued_payload_converts_without_caller_formula(
    override: dict[str, Any],
) -> None:
    public = server.OverrideIn.model_validate(override)
    patch = public.to_runtime()
    assert (patch.repo, patch.file_relative, patch.parameter) == public.target
    if public.patch_kind == "set_scalar":
        assert patch.patch_kind == "set_formula"
        assert re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", patch.formula)
        assert float(patch.formula) == public.value
    else:
        assert patch.patch_kind == public.patch_kind
        assert patch.multiplier == public.multiplier
        assert patch.formula is None
        assert patch.values is None


@settings(max_examples=100, deadline=None)
@given(file_relative=_target_strings, parameter=_parameter_strings)
@example(file_relative=SNAP_FILE, parameter=SNAP_TABLE)
def test_invariant_arbitrary_strings_refuse_or_map_to_exact_allowlist(
    file_relative: str, parameter: str
) -> None:
    try:
        override = server.OverrideIn.model_validate(
            {**VALID, "file_relative": file_relative, "parameter": parameter}
        )
    except ValidationError:
        return
    assert (override.repo, override.file_relative, override.parameter) in contract.ALLOWED_TARGETS


@settings(max_examples=50, deadline=None)
@given(candidate=_candidate)
@example(candidate=VALID)
def test_invariant_validation_never_touches_filesystem(candidate: dict[str, Any]) -> None:
    calls: list[str] = []

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        calls.append("filesystem")
        raise AssertionError("public validation must be a pure lookup and numeric check")

    # Context-local patches avoid interfering with Hypothesis' example database.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        for name in ("open", "stat", "lstat", "listdir", "scandir"):
            patch.setattr(os, name, forbidden)
        for name in ("open", "exists", "stat", "resolve", "read_text", "read_bytes"):
            patch.setattr(Path, name, forbidden)
        try:
            server.MicrosimRequest.model_validate({"overrides": [candidate]})
            server.CompareRequest.model_validate(
                {"program": "federal-ctc", "overrides": [PE_VALID]}
            )
        except ValidationError:
            pass
    # Pydantic can wrap an AssertionError raised inside a validator as a
    # ValidationError. Observe the call as well as raising from the sentinel.
    assert not calls
