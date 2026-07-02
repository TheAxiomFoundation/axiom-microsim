"""Pinned populace-US population artifact: resolve, verify, and read.

Axiom's microsim reads its population from PolicyEngine's **populace**
data project rather than the deprecated ``policyengine-us-data`` Enhanced
CPS path. This module owns:

1. The **pin** — a specific, immutable populace-US release (repo, file,
   git-tag revision, sha256) exposed as module constants.
2. **Resolution** — turn the pin into a local file path, downloading it
   from Hugging Face (dataset repo) and verifying its sha256 before any
   consumer is allowed to read it.
3. **Reading** — populace ``.h5`` files are pandas ``HDFStore`` /
   PyTables tables (one compound-dtype dataset per entity), *not* the
   flat ``variable/year`` groups the legacy Enhanced CPS used. This
   module presents the populace layout to the loader as the same
   ``{column_name: ndarray}`` dictionary the flat reader produced, so
   the state/tax-unit filtering logic in :mod:`ecps_loader` is unchanged.

Why this exact pin, and NOT Hugging Face ``latest``
---------------------------------------------------
As of 2026-07-02, populace's ``latest.json`` and PolicyEngine bundle
4.18.8 point at the *sparse* refit artifact
``populace-us-2024-sparse-l0-refit-57k-...-national-only-20260701``. That
artifact **zeroes untargeted input bases** — IRA/HSA/self-employed
pension/childcare and other engine inputs come back all-zero
(PolicyEngine/populace#278, closed 2026-07-02 by pipeline-fix PR #279).
A rebuilt *sparse* artifact incorporating #279 has **not** been published
or certified yet. Running a microsim on the sparse artifact would
silently drop those bases and understate liabilities/benefits that
depend on them.

We therefore pin the **dense** ``f0af251`` release — the last certified
dense US population dataset (it was PolicyEngine's certified default until
2026-06-26). Its #278-class columns carry real mass (verified: taxable
IRA distributions ~$720M, self-employed pension ALD ~$115M, HSA ALD
~$8.5M, pre-subsidy childcare ~$40M, tip income ~$5.7M).

UPGRADE NOTE (remove this pin only when the condition below is met)
------------------------------------------------------------------
Bump :data:`POPULACE_US_REVISION` / :data:`POPULACE_US_SHA256` to a newer
populace-US release **only after** a *post-#279 sparse (or newer dense)
release is certified* — i.e. one whose IRA/HSA/SE-pension/childcare input
bases are confirmed non-zero. Do not chase Hugging Face ``latest``: the
current latest is the #278 sparse artifact and would regress every
microsim number. Verify the successor's #278-class column mass (see
``scripts``/PR body method) before repinning.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import h5py
import numpy as np

# --- The pin ----------------------------------------------------------------
#
# Verified 2026-07-02 against policyengine.py's certified-bundle git history
# (commit c39b9d0b, "Certify latest US Populace bundle", records exactly this
# revision+sha256 pairing for populace_us_2024.h5 as the pre-change default)
# and confirmed by downloading the artifact and re-hashing it.

POPULACE_US_REPO = "policyengine/populace-us"
POPULACE_US_FILENAME = "populace_us_2024.h5"
POPULACE_US_REVISION = "populace-us-2024-f0af251-703bd81a565c-20260620T201958Z"
POPULACE_US_SHA256 = "16be6338f9d0b3c339883dae59949e995663b64cf145de6728b3dd0f916c5d5f"
# Built with policyengine-us 1.729.0 (informational only — the microsim
# reads the raw h5 with h5py, so package compatibility is soft).
POPULACE_US_BUILT_WITH = "policyengine-us==1.729.0"

# Env override for a local/dev copy of the pinned artifact. When set, the
# file it points at is still sha256-verified against POPULACE_US_SHA256 —
# an override lets you avoid the download, not skip verification.
POPULACE_ENV_VAR = "AXIOM_POPULACE_US_H5"

# populace stores a single time period; the file *is* one year.
POPULACE_YEAR = "2024"

# Every populace top-level group is an entity whose ``table`` dataset holds
# that entity's columns as compound-dtype fields.
_ENTITY_GROUPS = (
    "person",
    "household",
    "tax_unit",
    "family",
    "spm_unit",
    "marital_unit",
)


class PopulaceVerificationError(RuntimeError):
    """Raised when the populace artifact fails sha256 verification."""


def sha256_of_file(path: Path | str, *, chunk_size: int = 1 << 20) -> str:
    """Streamed sha256 of a file (populace artifacts are ~350 MB)."""
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify_sha256(path: Path) -> None:
    actual = sha256_of_file(path)
    if actual != POPULACE_US_SHA256:
        raise PopulaceVerificationError(
            "artifact hash mismatch — refusing to run on unverified "
            f"population data. Expected sha256 {POPULACE_US_SHA256} for "
            f"{POPULACE_US_REPO}@{POPULACE_US_REVISION}/{POPULACE_US_FILENAME}, "
            f"but {path} hashed to {actual}."
        )


def _download_pinned() -> Path:
    """Download the pinned populace artifact from Hugging Face (dataset repo).

    Uses ``hf_hub_download`` with an explicit ``revision`` so the pin is
    honoured. The ``populace-data`` package's public ``load()`` (v0.1.0)
    cannot pin a revision, so we go through ``huggingface_hub`` directly —
    the same fallback path policyengine.py uses for dataset-type populace
    repos.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "huggingface_hub is required to fetch the pinned populace "
            "artifact. Install it (`uv pip install huggingface_hub`) or set "
            f"{POPULACE_ENV_VAR} to a local copy of {POPULACE_US_FILENAME}."
        ) from exc

    local = hf_hub_download(
        repo_id=POPULACE_US_REPO,
        repo_type="dataset",
        filename=POPULACE_US_FILENAME,
        revision=POPULACE_US_REVISION,
    )
    return Path(local)


def resolve_populace_path(*, verify: bool = True) -> Path:
    """Return a local path to the **verified** pinned populace-US artifact.

    Resolution priority:

    1. ``$AXIOM_POPULACE_US_H5`` — a local/dev copy of the pinned file.
       Still sha256-verified (the override skips the download, not the
       integrity check).
    2. The pinned ``hf_hub_download`` of
       :data:`POPULACE_US_REPO`@:data:`POPULACE_US_REVISION`, then
       sha256-verified.

    There is **no unpinned fallback**: we never silently resolve to
    Hugging Face ``latest`` (which is the #278 sparse artifact). A hash
    mismatch raises :class:`PopulaceVerificationError` rather than running
    on unverified population data.
    """
    override = os.environ.get(POPULACE_ENV_VAR)
    if override:
        path = Path(override).expanduser()
        if not path.exists():
            raise FileNotFoundError(
                f"{POPULACE_ENV_VAR}={override!r} does not exist. Unset it to "
                "download the pinned populace artifact, or point it at a local "
                f"copy of {POPULACE_US_FILENAME} "
                f"({POPULACE_US_REPO}@{POPULACE_US_REVISION})."
            )
    else:
        path = _download_pinned()

    if verify:
        _verify_sha256(path)
    return path


# --- Reading the populace HDFStore layout -----------------------------------


class PopulaceReader:
    """Read populace entity tables as flat ``{column: ndarray}`` dicts.

    populace ``.h5`` files store one compound-dtype ``table`` dataset per
    entity (``person/table``, ``household/table``, …). A column such as
    ``age`` is the ``age`` field of ``person/table``; ``household_weight``
    is a field of ``household/table``.

    This reader exposes :meth:`column`, which returns the 1-D array for a
    variable name by locating whichever entity table carries it. That is
    the exact shape the legacy flat reader produced from
    ``f[var][year][...]``, so downstream filtering is source-agnostic.

    A handful of variable names the microsim projections use are
    PolicyEngine-*derived* (computed by a formula, not stored as an input)
    and therefore absent from populace's input layer. For those we map to
    the raw input the derived variable is built from. Each alias is a
    deliberate, documented substitution — see :data:`DERIVED_TO_INPUT`.
    """

    #: Microsim column name -> populace raw-input field, for names that are
    #: PolicyEngine-derived (so not stored in populace's input layer).
    #:
    #: - ``rent``: PE's ``rent`` is post-housing-subsidy and SPM-folded
    #:   (``variables/household/expense/housing/rent.py``); its raw input is
    #:   ``pre_subsidy_rent``. For the SNAP shelter deduction, gross
    #:   (pre-subsidy) rent is the honest available substitute.
    #: - ``taxable_unemployment_compensation``: PE derives this from
    #:   ``unemployment_compensation`` via the §85 taxable-UI chain
    #:   (mostly ~100% taxable); the raw benefit is the available proxy.
    DERIVED_TO_INPUT: dict[str, str] = {
        "rent": "pre_subsidy_rent",
        "taxable_unemployment_compensation": "unemployment_compensation",
    }

    def __init__(self, h5: h5py.File):
        self._h5 = h5
        # Map every available field name -> entity group that carries it.
        self._field_owner: dict[str, str] = {}
        for entity in _ENTITY_GROUPS:
            if entity not in h5:
                continue
            table = h5[entity].get("table")
            if table is None or table.dtype.names is None:
                continue
            for field in table.dtype.names:
                # First owner wins; entity order is person-first so the
                # person-level copy of a shared name is preferred.
                self._field_owner.setdefault(field, entity)
        # Cache entity tables lazily (each is a single structured read).
        self._table_cache: dict[str, np.ndarray] = {}

    def _table(self, entity: str) -> np.ndarray:
        cached = self._table_cache.get(entity)
        if cached is None:
            cached = self._h5[entity]["table"][:]
            self._table_cache[entity] = cached
        return cached

    def has(self, var: str) -> bool:
        return var in self._field_owner or var in self.DERIVED_TO_INPUT

    def column(self, var: str) -> np.ndarray:
        """Return the 1-D array for ``var`` (with derived-name aliasing)."""
        source = var
        if var not in self._field_owner and var in self.DERIVED_TO_INPUT:
            source = self.DERIVED_TO_INPUT[var]
        entity = self._field_owner.get(source)
        if entity is None:
            raise KeyError(
                f"variable {var!r} not in populace file "
                f"({POPULACE_US_REPO}@{POPULACE_US_REVISION}); no such field "
                "on any entity table and no derived-input alias."
            )
        return np.asarray(self._table(entity)[source])
