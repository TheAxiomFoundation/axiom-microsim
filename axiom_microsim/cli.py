"""Command-line entry point.

::

    axiom-microsim run --program co-snap --state CO --year 2026
    axiom-microsim run --program co-snap --state CO \
        --override rules-us-co/policies/cdhs/snap/...:max_allotment:scale_values:0.95
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .aggregate.cost import aggregate as aggregate_cost
from .aggregate.distribution import by_household_income_decile
from .aggregate.reform import compare as compare_reform
from .data.ecps_loader import load_state
from .run.microsim import ParameterOverride, run_co_snap


def _parse_override(token: str) -> ParameterOverride:
    """Format: REPO/FILE_REL:PARAM:KIND:ARG

    Examples:
      rules-us-co/policies/cdhs/snap/x.yaml:max_allotment:scale_values:0.95
      rules-us-co/.../x.yaml:max_allotment:set_formula:1234.5
    """
    head, kind, arg = token.rsplit(":", 2)
    repo_file, parameter = head.split(":", 1)
    repo, file_relative = repo_file.split("/", 1)
    if repo not in {"rules-us", "rules-us-co"}:
        raise argparse.ArgumentTypeError(f"unknown repo {repo!r}")
    if kind in {"scale_values", "scale_formula"}:
        return ParameterOverride(
            repo=repo, file_relative=file_relative, parameter=parameter,
            patch_kind=kind, multiplier=float(arg),
        )
    if kind == "set_formula":
        return ParameterOverride(
            repo=repo, file_relative=file_relative, parameter=parameter,
            patch_kind=kind, formula=arg,
        )
    if kind == "set_values":
        # arg: "key=val,key=val"
        values = {int(k): float(v) for k, v in (kv.split("=") for kv in arg.split(","))}
        return ParameterOverride(
            repo=repo, file_relative=file_relative, parameter=parameter,
            patch_kind=kind, values=values,
        )
    raise argparse.ArgumentTypeError(f"unknown patch kind {kind!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="axiom-microsim")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run a microsim and print aggregates as JSON")
    run.add_argument("--program", default="co-snap")
    run.add_argument("--state", default="CO")
    run.add_argument("--year", type=int, default=2026)
    run.add_argument(
        "--override", action="append", type=_parse_override, default=[],
        help="Reform override (REPO/FILE:PARAM:KIND:ARG); can repeat. "
             "If any --override is given, the run also returns reform-vs-baseline impact.",
    )

    args = parser.parse_args(argv)

    if args.cmd == "run":
        if args.program != "co-snap":
            parser.error(f"v1 only supports --program co-snap (got {args.program!r})")

        batch = load_state(args.state)
        baseline = run_co_snap(batch, period_year=args.year)
        cost = aggregate_cost(baseline)
        dist = by_household_income_decile(baseline, batch)

        out: dict[str, Any] = {
            "program": baseline.program,
            "state": baseline.state,
            "period_year": baseline.period_year,
            "n_households_sampled": baseline.n_households,
            "n_persons_sampled": baseline.n_persons,
            "households_total_weighted": cost.households_total_weighted,
            "baseline": {
                "annual_cost": cost.total_annual_cost,
                "monthly_cost": cost.total_monthly_cost,
                "households_with_benefit": cost.households_with_benefit,
                "average_monthly_benefit": cost.average_monthly_benefit,
                "decile_distribution": [b.__dict__ for b in dist.bins],
            },
        }

        if args.override:
            reform = run_co_snap(batch, period_year=args.year, overrides=args.override)
            impact = compare_reform(baseline, reform)
            out["reform"] = impact.__dict__

        json.dump(out, sys.stdout, indent=2, default=float)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
