"""Pure, finite policy-lever validation for the two public POST routes.

This module deliberately does not read rule files. The catalogue is pinned to
the web sliders and rules-us revision in modal_app.py; see ISSUE_10_REPORT.md
for the source of each bound. Local ParameterOverride remains unrestricted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

if TYPE_CHECKING:
    from .run.microsim import ParameterOverride

MAX_OVERRIDES = 16
MAX_BODY_BYTES = 16 * 1024

Program = Literal["co-snap", "federal-income-tax", "federal-ctc"]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Target = tuple[str, str, str]

SNAP_MAX = "policies/usda/snap/fy-2026-cola/maximum-allotments.yaml"
SNAP_DEDUCTIONS = "policies/usda/snap/fy-2026-cola/deductions.yaml"
TAX_BRACKETS = "policies/irs/rev-proc-2025-32/income-tax-brackets.yaml"
CTC = "statutes/26/24/h.yaml"


@dataclass(frozen=True)
class LeverPolicy:
    program: str
    kinds: tuple[str, ...]
    minimum: float
    maximum: float
    # Baselines give both exact allowed keys and per-key set_values bounds.
    rows: tuple[float, ...] = ()
    scalar_baseline: int = 0


ALLOWED_TARGETS = MappingProxyType(
    {
        ("rules-us", SNAP_MAX, "snap_maximum_allotment_table"): LeverPolicy(
            "co-snap",
            ("scale_values", "set_values"),
            0.5,
            2,
            (298, 546, 785, 994, 1183, 1421, 1571, 1789),
        ),
        ("rules-us", SNAP_MAX, "snap_maximum_allotment_additional_member"): LeverPolicy(
            "co-snap",
            ("scale_scalar",),
            0.5,
            2,
            scalar_baseline=218,
        ),
        ("rules-us", SNAP_DEDUCTIONS, "snap_standard_deduction_48_states_dc_table"): LeverPolicy(
            "co-snap",
            ("scale_values", "set_values"),
            0,
            2,
            (209, 209, 209, 223, 261, 299),
        ),
        ("rules-us", TAX_BRACKETS, "income_tax_bracket_rates"): LeverPolicy(
            "federal-income-tax",
            ("scale_values", "set_values"),
            0.5,
            1.5,
            (0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37),
        ),
        ("rules-us", TAX_BRACKETS, "income_tax_bracket_thresholds_joint"): LeverPolicy(
            "federal-income-tax",
            ("scale_values", "set_values"),
            0.5,
            2,
            (24800, 100800, 211400, 403550, 512450, 768700),
        ),
        ("rules-us", TAX_BRACKETS, "income_tax_bracket_thresholds_single"): LeverPolicy(
            "federal-income-tax",
            ("scale_values", "set_values"),
            0.5,
            2,
            (12400, 50400, 105700, 201775, 256225, 640600),
        ),
        ("rules-us", CTC, "ctc_child_amount_under_subsection_h"): LeverPolicy(
            "federal-ctc",
            ("set_scalar",),
            0,
            6000,
        ),
        ("rules-us", CTC, "ctc_other_dependent_amount_under_subsection_h"): LeverPolicy(
            "federal-ctc",
            ("set_scalar",),
            0,
            2500,
        ),
        ("rules-us", CTC, "ctc_joint_phase_out_threshold_under_subsection_h"): LeverPolicy(
            "federal-ctc",
            ("set_scalar",),
            50000,
            1000000,
        ),
    }
)


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverrideIn(PublicModel):
    repo: Literal["rules-us"]
    file_relative: str = Field(max_length=200)
    parameter: str = Field(max_length=100)
    patch_kind: Literal["scale_values", "set_values", "scale_scalar", "set_scalar"]
    multiplier: Number | None = None
    values: dict[int, Number] | None = Field(default=None, max_length=8)
    value: Number | None = None

    @field_validator("file_relative")
    @classmethod
    def allowed_file(cls, value: str, info: ValidationInfo) -> str:
        if not any((info.data.get("repo"), value) == key[:2] for key in ALLOWED_TARGETS):
            raise ValueError("file_relative must name an allowlisted rule file in repo")
        return value

    @field_validator("parameter")
    @classmethod
    def allowed_parameter(cls, value: str, info: ValidationInfo) -> str:
        if (info.data.get("repo"), info.data.get("file_relative"), value) not in ALLOWED_TARGETS:
            raise ValueError("parameter must name an allowlisted target in file_relative")
        return value

    @property
    def target(self) -> Target:
        return self.repo, self.file_relative, self.parameter

    @model_validator(mode="after")
    def bounded_patch(self) -> OverrideIn:
        policy = ALLOWED_TARGETS[self.target]
        if self.patch_kind not in policy.kinds:
            raise ValueError("patch_kind is not allowed for parameter")
        payload = (
            "values"
            if self.patch_kind == "set_values"
            else "value"
            if self.patch_kind == "set_scalar"
            else "multiplier"
        )
        unexpected = (self.model_fields_set & {"multiplier", "values", "value"}) - {payload}
        if unexpected:
            raise ValueError(f"{', '.join(sorted(unexpected))} is not allowed for patch_kind")
        if payload == "values":
            if not self.values:
                raise ValueError("values must contain at least one allowed key")
            for key, value in self.values.items():
                if key < 1 or key > len(policy.rows):
                    raise ValueError("values contains a key not allowed for parameter")
                baseline = policy.rows[key - 1]
                if not baseline * policy.minimum <= value <= baseline * policy.maximum:
                    raise ValueError(f"values[{key}] is outside the allowed range")
        else:
            value = getattr(self, payload)
            if value is None or not policy.minimum <= value <= policy.maximum:
                raise ValueError(f"{payload} must be between {policy.minimum} and {policy.maximum}")
        return self

    def to_runtime(self) -> ParameterOverride:
        # Only the server formats numeric constants as RuleSpec. No caller
        # string can become a formula; the runtime's local API stays unchanged.
        from .run.microsim import ParameterOverride

        if self.patch_kind in {"set_scalar", "scale_scalar"}:
            value = (
                Decimal(str(self.value))
                if self.patch_kind == "set_scalar"
                else Decimal(ALLOWED_TARGETS[self.target].scalar_baseline)
                * Decimal(str(self.multiplier))
            )
            return ParameterOverride(
                repo=self.repo,
                file_relative=self.file_relative,
                parameter=self.parameter,
                patch_kind="set_formula",
                formula=format(value, "f"),
            )
        if self.patch_kind == "scale_values" and self.multiplier == 0:
            # The local legacy patcher uses `multiplier or 1.0`. Express the
            # public zero endpoint explicitly, without changing that local API.
            return ParameterOverride(
                repo=self.repo,
                file_relative=self.file_relative,
                parameter=self.parameter,
                patch_kind="set_values",
                values={key: 0.0 for key in range(1, len(ALLOWED_TARGETS[self.target].rows) + 1)},
            )
        return ParameterOverride(
            repo=self.repo,
            file_relative=self.file_relative,
            parameter=self.parameter,
            patch_kind=self.patch_kind,
            multiplier=self.multiplier,
            values=self.values,
        )


class MicrosimRequest(PublicModel):
    program: Program = "co-snap"
    state: str = "CO"
    year: int = 2026
    overrides: list[OverrideIn] = Field(default_factory=list, max_length=MAX_OVERRIDES)

    @model_validator(mode="after")
    def overrides_for_program(self) -> MicrosimRequest:
        seen = set()
        for override in self.overrides:
            if ALLOWED_TARGETS[override.target].program != self.program:
                raise ValueError("overrides.parameter is not allowed for program")
            if override.target in seen:
                raise ValueError("overrides contains a duplicate parameter target")
            seen.add(override.target)
        return self


PE_ALLOWED_PATHS = MappingProxyType(
    {
        "gov.irs.credits.ctc.amount.base[0].amount": ("federal-ctc", 0, 6000),
        "gov.irs.credits.ctc.amount.adult_dependent": ("federal-ctc", 0, 2500),
        "gov.irs.credits.ctc.phase_out.threshold.JOINT": ("federal-ctc", 50000, 1000000),
        **{
            f"gov.irs.income.bracket.rates.{i}": ("federal-income-tax", rate * 0.5, rate * 1.5)
            for i, rate in enumerate((0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37), 1)
        },
    }
)


class PeOverrideIn(PublicModel):
    path: str = Field(max_length=120)
    value: Number

    @field_validator("path")
    @classmethod
    def allowed_path(cls, value: str) -> str:
        if value not in PE_ALLOWED_PATHS:
            raise ValueError("path must name an allowlisted PE parameter")
        return value

    @model_validator(mode="after")
    def bounded_value(self) -> PeOverrideIn:
        _, minimum, maximum = PE_ALLOWED_PATHS[self.path]
        if not minimum <= self.value <= maximum:
            raise ValueError(f"value must be between {minimum} and {maximum}")
        return self


class CompareRequest(PublicModel):
    program: Program
    state: str = "US"
    year: int = 2026
    overrides: list[PeOverrideIn] = Field(default_factory=list, max_length=MAX_OVERRIDES)

    @model_validator(mode="after")
    def overrides_for_program(self) -> CompareRequest:
        seen = set()
        for override in self.overrides:
            if PE_ALLOWED_PATHS[override.path][0] != self.program:
                raise ValueError("overrides.path is not allowed for program")
            if override.path in seen:
                raise ValueError("overrides contains a duplicate path")
            seen.add(override.path)
        return self
