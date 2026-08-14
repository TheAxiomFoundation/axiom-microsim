"""The declared runtime, the tested runtime, and the deployed runtime must agree.

`requires-python` (the contract), the CI `uv python install` (what gets tested),
and `modal_app.py`'s `debian_slim(python_version=...)` (what actually runs in
production) are three independent declarations of one Python minor version. When
they drift, CI-green code can still fail at import on Modal — e.g. PEP 758
parenless `except` parses on 3.14 but raises `SyntaxError` on 3.13, and CI never
sees it because CI only runs the version it installs.

This test fails loudly on any drift, so the mismatch surfaces as a red check
instead of a production import error on the next deploy.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requires_python_minor() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    spec = data["project"]["requires-python"]  # e.g. "==3.14.*"
    m = re.search(r"(\d+\.\d+)", spec)
    assert m, f"could not parse a X.Y version from requires-python = {spec!r}"
    return m.group(1)


def _modal_python_minor() -> str:
    text = (ROOT / "modal_app.py").read_text()
    m = re.search(r"debian_slim\(\s*python_version\s*=\s*[\"'](\d+\.\d+)[\"']", text)
    assert m, "could not find debian_slim(python_version=...) in modal_app.py"
    return m.group(1)


def _ci_python_minor() -> str:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    m = re.search(r"uv python install\s+(\d+\.\d+)", text)
    assert m, "could not find `uv python install X.Y` in ci.yml"
    return m.group(1)


def test_declared_tested_and_deployed_python_agree() -> None:
    contract = _requires_python_minor()
    tested = _ci_python_minor()
    deployed = _modal_python_minor()
    assert contract == tested == deployed, (
        "Python minor version drift across the three sources of truth:\n"
        f"  pyproject requires-python : {contract}\n"
        f"  CI uv python install      : {tested}\n"
        f"  modal_app debian_slim     : {deployed}\n"
        "CI tests only its own version, so a mismatch lets CI-green code fail at "
        "import on Modal (e.g. PEP 758 parenless `except` on 3.14 vs 3.13). "
        "Align all three."
    )
