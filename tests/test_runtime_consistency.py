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

import ast
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


def test_modal_image_enforces_requires_python() -> None:
    # `pip install --ignore-requires-python` is how a 3.13 image installed a
    # `==3.14.*` package without complaint. Without the flag, the image build
    # itself rejects a Python that breaks the contract.
    text = (ROOT / "modal_app.py").read_text()
    assert "--ignore-requires-python" not in text, (
        "modal_app.py bypasses requires-python inside the image; drop "
        "--ignore-requires-python so a wrong image Python fails at build time."
    )


def test_modal_web_has_a_bounded_container_limit() -> None:
    tree = ast.parse((ROOT / "modal_app.py").read_text())
    web = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "web"
    )
    decorators = [
        node
        for node in web.decorator_list
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "app"
        and node.func.attr == "function"
    ]
    assert len(decorators) == 1, "web must have exactly one @app.function(...) decorator"
    limits = [
        keyword.value for keyword in decorators[0].keywords if keyword.arg == "max_containers"
    ]
    assert len(limits) == 1, "web must explicitly cap max_containers"
    limit = ast.literal_eval(limits[0])
    assert type(limit) is int and 1 <= limit <= 2, (
        "web max_containers must be an integer from 1 to 2"
    )


def test_readme_setup_names_the_contract_python() -> None:
    contract = _requires_python_minor()
    text = (ROOT / "README.md").read_text()
    named = re.findall(r"(?:python@|--python\s+)(\d+\.\d+)", text)
    assert named, "README.md no longer names a Python version for local setup"
    stale = sorted(set(named) - {contract})
    assert not stale, (
        f"README.md setup names Python {', '.join(stale)}, but requires-python "
        f"pins {contract}. Update the brew / `uv venv --python` lines."
    )
