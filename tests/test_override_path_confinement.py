"""``file_relative`` comes from the public HTTP surface, so it must not escape.

Each test states the escape it blocks. The positive cases pin that ordinary
relative targets — including nested ones — still resolve, so the guard cannot
be "fixed" by refusing everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_microsim.run.microsim import _resolve_override_target


def test_plain_relative_path_resolves(tmp_path: Path) -> None:
    target = _resolve_override_target(tmp_path, "policies/snap/fy-2026.yaml")
    assert target == (tmp_path.resolve() / "policies/snap/fy-2026.yaml")


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    # `root / "/etc/passwd"` silently discards root and yields /etc/passwd.
    with pytest.raises(ValueError, match="must be a relative path"):
        _resolve_override_target(tmp_path, "/etc/passwd")


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not contain"):
        _resolve_override_target(tmp_path, "../../etc/passwd")


def test_embedded_parent_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not contain"):
        _resolve_override_target(tmp_path, "policies/../../../etc/passwd")


def test_empty_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a relative path"):
        _resolve_override_target(tmp_path, "")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    # A symlink inside the rules root pointing out of it must not become a
    # write path: the `..`-free check passes, so resolution has to catch it.
    root = tmp_path / "rules"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.yaml").write_text("rules: []\n")
    (root / "escape").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes the rules root"):
        _resolve_override_target(root, "escape/secret.yaml")


def test_symlink_within_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "rules"
    (root / "real").mkdir(parents=True)
    (root / "real" / "f.yaml").write_text("rules: []\n")
    (root / "link").symlink_to(root / "real")

    target = _resolve_override_target(root, "link/f.yaml")
    assert target == (root.resolve() / "real" / "f.yaml")
