"""Unit tests for the pinned populace resolver, verifier, and reader.

These run without any real data file: the download is mocked, and the
HDFStore-layout reader is exercised against a tiny synthetic file built
in a tmp dir. The one integration behaviour that needs real bytes
(sha256 of the pinned artifact) is covered by hashing a stand-in file
whose digest we compute on the fly.
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import h5py
import numpy as np
import pytest

from axiom_microsim.data import ecps_loader as el
from axiom_microsim.data import populace_loader as pl


# --- Pin constants are exported and well-formed ------------------------------


def test_pin_constants_exported() -> None:
    assert pl.POPULACE_US_REPO == "policyengine/populace-us"
    assert pl.POPULACE_US_FILENAME == "populace_us_2024.h5"
    assert pl.POPULACE_US_REVISION == ("populace-us-2024-f0af251-703bd81a565c-20260620T201958Z")
    # sha256 is 64 lowercase hex chars.
    assert len(pl.POPULACE_US_SHA256) == 64
    assert all(c in "0123456789abcdef" for c in pl.POPULACE_US_SHA256)


# --- sha256 verification -----------------------------------------------------


def _write_bytes(tmp_path: Path, data: bytes, name: str = "populace_us_2024.h5") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_sha256_of_file_matches_hashlib(tmp_path: Path) -> None:
    data = b"some population bytes" * 1000
    p = _write_bytes(tmp_path, data)
    assert pl.sha256_of_file(p) == hashlib.sha256(data).hexdigest()


def test_override_hash_mismatch_refuses_to_run(tmp_path: Path, monkeypatch) -> None:
    bad = _write_bytes(tmp_path, b"not the pinned artifact")
    monkeypatch.setenv(pl.POPULACE_ENV_VAR, str(bad))
    with pytest.raises(pl.PopulaceVerificationError) as exc:
        pl.resolve_populace_path()
    msg = str(exc.value)
    assert "artifact hash mismatch" in msg
    assert "refusing to run on unverified population data" in msg


def test_override_verify_false_skips_check(tmp_path: Path, monkeypatch) -> None:
    bad = _write_bytes(tmp_path, b"not the pinned artifact")
    monkeypatch.setenv(pl.POPULACE_ENV_VAR, str(bad))
    # verify=False is a deliberate dev escape; must not raise.
    assert pl.resolve_populace_path(verify=False) == bad


def test_override_matching_hash_passes(tmp_path: Path, monkeypatch) -> None:
    data = b"pretend this is the pinned artifact"
    good = _write_bytes(tmp_path, data)
    # Pin the module's expected hash to this file's digest for the test.
    monkeypatch.setattr(pl, "POPULACE_US_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setenv(pl.POPULACE_ENV_VAR, str(good))
    assert pl.resolve_populace_path() == good


def test_missing_override_file_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(pl.POPULACE_ENV_VAR, str(tmp_path / "does-not-exist.h5"))
    with pytest.raises(FileNotFoundError):
        pl.resolve_populace_path()


# --- Pinned download path (mocked) uses the right HF arguments ---------------


def test_pinned_download_uses_dataset_repo_and_revision(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(pl.POPULACE_ENV_VAR, raising=False)
    data = b"downloaded pinned artifact"
    fake = _write_bytes(tmp_path, data)
    captured = {}

    def fake_download(*, repo_id, repo_type, filename, revision):
        captured.update(repo_id=repo_id, repo_type=repo_type, filename=filename, revision=revision)
        return str(fake)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(pl, "POPULACE_US_SHA256", hashlib.sha256(data).hexdigest())

    out = pl.resolve_populace_path()
    assert out == fake
    assert captured == {
        "repo_id": "policyengine/populace-us",
        "repo_type": "dataset",
        "filename": "populace_us_2024.h5",
        "revision": "populace-us-2024-f0af251-703bd81a565c-20260620T201958Z",
    }


# --- HDFStore-layout reader + derived-name aliasing --------------------------


def _make_populace_like(tmp_path: Path) -> Path:
    """Build a minimal populace-layout h5: entity/table compound datasets."""
    p = tmp_path / "mini_populace.h5"
    person = np.zeros(
        4,
        dtype=[
            ("index", "i8"),
            ("age", "f8"),
            ("person_household_id", "i8"),
            ("person_tax_unit_id", "i8"),
            ("employment_income_before_lsr", "f8"),
            ("pre_subsidy_rent", "f8"),
            ("unemployment_compensation", "f8"),
        ],
    )
    person["index"] = [0, 1, 2, 3]
    person["age"] = [40, 38, 10, 66]
    person["person_household_id"] = [1, 1, 1, 2]
    person["person_tax_unit_id"] = [1, 1, 1, 2]
    person["employment_income_before_lsr"] = [50000, 30000, 0, 0]
    person["pre_subsidy_rent"] = [12000, 0, 0, 9000]
    person["unemployment_compensation"] = [0, 2500, 0, 0]

    household = np.zeros(
        2,
        dtype=[
            ("index", "i8"),
            ("household_id", "i8"),
            ("state_fips", "i8"),
            ("household_weight", "f8"),
        ],
    )
    household["index"] = [0, 1]
    household["household_id"] = [1, 2]
    household["state_fips"] = [8, 8]  # both CO
    household["household_weight"] = [1000.0, 2000.0]

    with h5py.File(p, "w") as f:
        f.create_dataset("person/table", data=person)
        f.create_dataset("household/table", data=household)
    return p


def test_reader_reads_fields_by_entity(tmp_path: Path) -> None:
    p = _make_populace_like(tmp_path)
    with h5py.File(p, "r") as f:
        r = pl.PopulaceReader(f)
        np.testing.assert_array_equal(r.column("age"), [40, 38, 10, 66])
        np.testing.assert_array_equal(r.column("household_weight"), [1000.0, 2000.0])
        assert r.has("employment_income_before_lsr")
        assert not r.has("no_such_variable")


def test_reader_aliases_derived_names(tmp_path: Path) -> None:
    p = _make_populace_like(tmp_path)
    with h5py.File(p, "r") as f:
        r = pl.PopulaceReader(f)
        # PE-derived names map to their populace raw inputs.
        np.testing.assert_array_equal(r.column("rent"), [12000, 0, 0, 9000])
        np.testing.assert_array_equal(
            r.column("taxable_unemployment_compensation"), [0, 2500, 0, 0]
        )
        assert r.has("rent")
        assert r.has("taxable_unemployment_compensation")


def test_reader_unknown_variable_raises(tmp_path: Path) -> None:
    p = _make_populace_like(tmp_path)
    with h5py.File(p, "r") as f:
        r = pl.PopulaceReader(f)
        with pytest.raises(KeyError):
            r.column("totally_made_up_field")


# --- Loader end-to-end over the synthetic populace file ----------------------


def test_load_state_over_synthetic_populace(tmp_path: Path, monkeypatch) -> None:
    p = _make_populace_like(tmp_path)
    # Route the loader to this file via the pinned-copy override, and
    # pin the expected hash to it so verification passes.
    monkeypatch.setattr(pl, "POPULACE_US_SHA256", pl.sha256_of_file(p))
    monkeypatch.setenv(pl.POPULACE_ENV_VAR, str(p))
    monkeypatch.delenv("AXIOM_ECPS_PATH", raising=False)

    batch = el.load_state("CO", person_columns=("age", "rent"))
    assert batch.state == "CO"
    assert batch.n_households == 2
    assert batch.n_persons == 4
    # rent aliased to pre_subsidy_rent, kept per person.
    np.testing.assert_array_equal(batch.person_columns["rent"], [12000, 0, 0, 9000])
    np.testing.assert_array_equal(batch.household_weight, [1000.0, 2000.0])


# --- Source resolution order -------------------------------------------------


def test_explicit_path_sniffs_populace_layout(tmp_path: Path) -> None:
    p = _make_populace_like(tmp_path)
    with h5py.File(p, "r") as f:
        reader = el._reader_for(f)
    assert isinstance(reader, el._PopulaceColumnReader)


def test_explicit_path_sniffs_ecps_layout(tmp_path: Path) -> None:
    # Flat variable/year layout -> Enhanced CPS reader.
    ecps = tmp_path / "flat.h5"
    with h5py.File(ecps, "w") as f:
        f.create_dataset("age/2024", data=np.array([1.0, 2.0]))
    with h5py.File(ecps, "r") as f:
        reader = el._reader_for(f)
    assert isinstance(reader, el._EcpsColumnReader)


def test_ecps_env_var_emits_deprecation_warning(tmp_path: Path, monkeypatch) -> None:
    # A flat ECPS file present at AXIOM_ECPS_PATH should be used, with a
    # DeprecationWarning naming the migration.
    ecps = tmp_path / "flat.h5"
    with h5py.File(ecps, "w") as f:
        # Enough structure for _open_population to open + warn (reads happen
        # inside load_state; here we only assert the warning on open).
        f.create_dataset("household_id/2024", data=np.array([1, 2]))
    monkeypatch.delenv(pl.POPULACE_ENV_VAR, raising=False)
    monkeypatch.setenv("AXIOM_ECPS_PATH", str(ecps))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with el._open_population(None) as src:
            assert isinstance(src, el._EcpsColumnReader)
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, "expected a DeprecationWarning for the ECPS escape hatch"
    assert "populace" in str(deprecations[0].message)


def test_missing_ecps_env_file_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(pl.POPULACE_ENV_VAR, raising=False)
    monkeypatch.setenv("AXIOM_ECPS_PATH", str(tmp_path / "nope.h5"))
    with pytest.raises(FileNotFoundError):
        with el._open_population(None):
            pass


def test_explicit_missing_path_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        with el._open_population(tmp_path / "absent.h5"):
            pass
