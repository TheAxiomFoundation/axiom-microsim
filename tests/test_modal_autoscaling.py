"""The Modal web endpoint must scale to zero when idle.

A warm container bills every hour whether or not anyone calls it. With
``min_containers=1`` this app cost about $1.68/day at Modal's default CPU, and
about $10.8/day once the function reserved 8 cores, while serving a handful of
demo sessions a month (Modal workspace billing, May-Sep 2026). This test reads
``modal_app.py`` as source, so it runs without Modal installed, and fails if the
``web`` endpoint pins a warm container again.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _web_function_kwargs() -> dict[str, ast.expr]:
    tree = ast.parse((ROOT / "modal_app.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "web":
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "function"
                ):
                    return {kw.arg: kw.value for kw in dec.keywords if kw.arg}
    raise AssertionError("no @app.function(...) decorator on `web` in modal_app.py")


def test_web_endpoint_scales_to_zero() -> None:
    kwargs = _web_function_kwargs()
    assert "keep_warm" not in kwargs, (
        "`keep_warm` pins warm containers; use min_containers=0 instead."
    )
    min_containers = ast.literal_eval(kwargs.get("min_containers", ast.Constant(0)))
    assert min_containers == 0, (
        f"modal_app.py sets min_containers={min_containers} on `web`. A warm "
        "container bills around the clock (about $1.68/day at the default CPU, "
        "about $10.8/day at cpu=8) for an endpoint that sees a few sessions a "
        "month. Keep min_containers=0 and rely on scaledown_window."
    )


def test_web_endpoint_keeps_a_container_between_clicks() -> None:
    kwargs = _web_function_kwargs()
    assert "scaledown_window" in kwargs, (
        "Set scaledown_window on `web` so one demo session pays one cold start, "
        "not one per request."
    )
    window = ast.literal_eval(kwargs["scaledown_window"])
    assert 60 <= window <= 1200, (
        f"scaledown_window={window}s; keep it between 1 and 20 minutes so an "
        "idle container still shuts down soon after a session ends."
    )
