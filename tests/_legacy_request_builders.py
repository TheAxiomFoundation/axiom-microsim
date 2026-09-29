"""The request builders as they were before tuple order came from the artifact.

Frozen copies of ``_build_ctc_request_bytes`` (minus its cache) and
``_build_compiled_request`` from axiom-microsim ``f88644c``. They hard-code the
related-entity-first tuple ``[person, owner]``. The differential tests assert
that the current builders reproduce them byte for byte whenever the artifact
aggregates in the legacy direction. Do not edit them to track the live code.
They share the value encoders and schema loader (``_scalar_value``,
``_input_record``, ``_input_id``, ``_slots``) with the live module, which this
change does not touch.
"""

from __future__ import annotations

import numpy as np
import orjson

from axiom_microsim.project.co_snap import CoSnapProjection
from axiom_microsim.project.federal_ctc import FedCtcProjection
from axiom_microsim.run.microsim import (
    CO_SNAP_RELATION_NAME,
    FED_CTC_OUTPUT_IDS,
    FED_CTC_RELATION_NAME,
    _input_id,
    _input_record,
    _scalar_value,
    _slots,
)


def legacy_build_ctc_request_bytes(
    projection: FedCtcProjection,
    period_year: int,
    output_names: tuple[str, ...],
) -> bytes:
    interval = {"start": f"{period_year}-01-01", "end": f"{period_year}-12-31"}
    period = {"period_kind": "tax_year", "start": interval["start"], "end": interval["end"]}
    output_ids = [FED_CTC_OUTPUT_IDS[n] for n in output_names]

    inputs: list[dict] = []
    relations: list[dict] = []
    queries: list[dict] = []

    for tu_idx in range(projection.n_tax_units):
        tu_id = f"tu{tu_idx}"
        for full_id, column in projection.tax_unit_inputs.items():
            inputs.append(
                {
                    "name": full_id,
                    "entity": "TaxUnit",
                    "entity_id": tu_id,
                    "interval": interval,
                    "value": _scalar_value(column[tu_idx]),
                }
            )
        queries.append({"entity_id": tu_id, "period": period, "outputs": output_ids})

    pos_in_sorted = np.arange(projection.n_persons)
    tu_for_person = np.searchsorted(projection.relation_offsets, pos_in_sorted, side="right") - 1

    for sorted_p_idx in range(projection.n_persons):
        person_id = f"p{sorted_p_idx}"
        tu_idx = int(tu_for_person[sorted_p_idx])
        tu_id = f"tu{tu_idx}"
        for full_id, column in projection.person_inputs.items():
            inputs.append(
                {
                    "name": full_id,
                    "entity": "Person",
                    "entity_id": person_id,
                    "interval": interval,
                    "value": _scalar_value(column[sorted_p_idx]),
                }
            )
        for full_id, column in projection.tax_unit_inputs.items():
            inputs.append(
                {
                    "name": full_id,
                    "entity": "Person",
                    "entity_id": person_id,
                    "interval": interval,
                    "value": _scalar_value(column[tu_idx]),
                }
            )
        relations.append(
            {
                "name": FED_CTC_RELATION_NAME,
                "tuple": [person_id, tu_id],
                "interval": interval,
            }
        )

    request = {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": relations},
        "queries": queries,
    }
    return orjson.dumps(request)


def legacy_build_compiled_request(
    proj: CoSnapProjection,
    period_year: int,
    output_ids: list[str],
    extra_household_inputs: dict[str, np.ndarray] | None = None,
) -> dict:
    interval = {"start": f"{period_year}-01-01", "end": f"{period_year}-01-31"}
    period = {
        "period_kind": "month",
        "start": f"{period_year}-01-01",
        "end": f"{period_year}-01-31",
    }

    inputs: list[dict] = []
    relations: list[dict] = []
    queries: list[dict] = []

    hh_slots, person_slots = _slots()

    for h_idx in range(proj.n_households):
        hh_id = f"h{h_idx}"
        for slot in hh_slots:
            value = (
                proj.household_inputs[slot.name][h_idx]
                if slot.name in proj.household_inputs
                else slot.default
            )
            inputs.append(_input_record(_input_id(slot.name), "Household", hh_id, interval, value))
        for input_id, values in (extra_household_inputs or {}).items():
            inputs.append(_input_record(input_id, "Household", hh_id, interval, values[h_idx]))
        queries.append(
            {
                "entity_id": hh_id,
                "period": period,
                "outputs": output_ids,
            }
        )

    person_to_hh = (
        np.searchsorted(proj.relation_offsets, np.arange(proj.n_persons), side="right") - 1
    )
    for p_idx in range(proj.n_persons):
        person_id = f"p{p_idx}"
        hh_id = f"h{int(person_to_hh[p_idx])}"
        for slot in person_slots:
            value = (
                proj.person_inputs[slot.name][p_idx]
                if slot.name in proj.person_inputs
                else slot.default
            )
            inputs.append(_input_record(_input_id(slot.name), "Person", person_id, interval, value))
        relations.append(
            {
                "name": CO_SNAP_RELATION_NAME,
                "tuple": [person_id, hh_id],
                "interval": interval,
            }
        )

    return {
        "mode": "fast",
        "dataset": {"inputs": inputs, "relations": relations},
        "queries": queries,
    }
