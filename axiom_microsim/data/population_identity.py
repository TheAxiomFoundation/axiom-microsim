"""Cheap cache identities for the population source used by the Axiom loaders."""

from __future__ import annotations

import os
from pathlib import Path

from . import populace_loader


def file_identity(path: Path) -> tuple:
    """Track replacements without reading a population or compiled artifact."""
    path = path.expanduser().resolve()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (str(path), None, None)
    return (str(path), stat.st_size, stat.st_mtime_ns)


def population_identity() -> tuple:
    # Match ecps_loader._open_population: an explicit legacy override wins.
    override = os.environ.get("AXIOM_ECPS_PATH") or os.environ.get("AXIOM_POPULACE_US_H5")
    if override:
        return file_identity(Path(override))

    # The route has already loaded the population before looking up a baseline.
    # Resolve the downloaded pin from the HF cache without network or a second
    # full-file sha256 verification. A missing local copy still has an immutable
    # pin identity (useful to callers that supply their own in-memory batch).
    from huggingface_hub import try_to_load_from_cache

    pin = (
        populace_loader.POPULACE_US_REPO,
        populace_loader.POPULACE_US_FILENAME,
        populace_loader.POPULACE_US_REVISION,
        populace_loader.POPULACE_US_SHA256,
    )
    local = try_to_load_from_cache(pin[0], pin[1], revision=pin[2], repo_type="dataset")
    return (*pin, file_identity(Path(local)) if isinstance(local, str) else None)
