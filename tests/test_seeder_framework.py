"""Tests for domain-agnostic seeder framework (B4)."""

import csv
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from seeders.base import Seeder
from seeders.registry import SeederRegistry


# --- Seeder ABC ---

class ConcreteSeeder(Seeder):
    name = "test_seeder"
    domain = "test"
    target_table = "test_table"
    source_tag = "test_source"
    schema_required = []

    def fetch(self):
        return [{"key": "val"}]

    def parse(self, payload):
        return [{"k": r["key"]} for r in payload]

    def upsert(self, conn, rows):
        return len(rows)


def test_seeder_abc_run():
    s = ConcreteSeeder()
    mock_conn = MagicMock()
    count = s.run(mock_conn)
    assert count == 1


def test_seeder_validate_schema_passes_when_no_requirements():
    s = ConcreteSeeder()
    mock_conn = MagicMock()
    s.validate_schema(mock_conn)  # should not raise


def test_seeder_validate_schema_raises_when_table_missing():
    class Strict(ConcreteSeeder):
        schema_required = ["nonexistent_table"]

    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = (False,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    s = Strict()
    with pytest.raises(AssertionError, match="nonexistent_table missing"):
        s.validate_schema(mock_conn)


# --- SeederRegistry ---

def _write_manifest(tmp_path, domain, seeders_yaml):
    (tmp_path / domain).mkdir(parents=True, exist_ok=True)
    manifest = tmp_path / domain / "manifest.yaml"
    manifest.write_text(
        f"domain: {domain}\ndescription: test\nschema_migrations: []\nseeders:\n{seeders_yaml}"
    )
    return str(tmp_path / domain / "manifest.yaml")


def test_seeder_registry_disabled_seeders_skipped():
    """Seeders with enabled: false should not appear in registry.seeders."""
    registry = SeederRegistry("real_estate")
    # statscan_shapefile has enabled: false in real manifest
    names = [s.name for s in registry.seeders]
    assert "statscan_shapefile" not in names


def test_seeder_registry_real_estate_loads():
    """Real manifest loads without error."""
    registry = SeederRegistry("real_estate")
    assert registry.domain == "real_estate"
    # spell_corrections seeder should load (enabled=true)
    assert any(s.name == "spell_corrections" for s in registry.seeders)
    # statscan disabled — should NOT be in seeders list
    assert not any(s.name == "statscan_shapefile" for s in registry.seeders)


def test_seeder_registry_unknown_domain_raises():
    with pytest.raises(FileNotFoundError, match="No manifest for domain"):
        SeederRegistry("nonexistent_domain_xyz")


def test_seeder_registry_dry_run(capsys):
    registry = SeederRegistry("real_estate")
    mock_conn = MagicMock()
    results = registry.run_all(mock_conn, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY" in out
    assert all(v == -1 for v in results.values())


def test_seeder_registry_only_filter():
    """run_all with only= skips other seeders."""
    registry = SeederRegistry("real_estate")
    mock_conn = MagicMock()

    # Only run a seeder that doesn't exist → empty results
    results = registry.run_all(mock_conn, only=["nonexistent_name"], dry_run=True)
    assert results == {}


# --- SpellCorrectionsSeeder ---

def test_spell_corrections_seeder_end_to_end(tmp_path):
    from seeders.real_estate.spell_corrections import SpellCorrectionsSeeder

    csv_path = tmp_path / "corrections.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["wrong", "right", "source", "confidence"])
        w.writeheader()
        w.writerows([
            {"wrong": "toronot", "right": "toronto", "source": "manual_seed", "confidence": "1.0"},
        ])

    seeder = SpellCorrectionsSeeder(config={"seed_csv": str(csv_path)})
    payload = seeder.fetch()
    rows = seeder.parse(payload)

    assert len(rows) == 1
    assert rows[0]["wrong"] == "toronot"
    assert rows[0]["right"] == "toronto"
    assert rows[0]["domain"] == "real_estate"

    inserted = []
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.executemany = lambda sql, params: inserted.extend(params)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    count = seeder.upsert(mock_conn, rows)
    assert count == 1
    assert inserted[0][0] == "toronot"
    mock_conn.commit.assert_called_once()


def test_spell_corrections_seeder_missing_file():
    from seeders.real_estate.spell_corrections import SpellCorrectionsSeeder
    seeder = SpellCorrectionsSeeder(config={"seed_csv": "/nonexistent.csv"})
    with pytest.raises(FileNotFoundError):
        seeder.fetch()


# --- scaffold_domain smoke test ---

def test_scaffold_domain_creates_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.scaffold_domain import scaffold
    scaffold("test_industry")

    assert (tmp_path / "skills" / "test_industry" / "skills.yaml").exists()
    assert (tmp_path / "seeders" / "test_industry" / "manifest.yaml").exists()
    assert (tmp_path / "data" / "seeds" / "test_industry" / "README.md").exists()


def test_scaffold_domain_skips_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts.scaffold_domain import scaffold

    scaffold("repeat_industry")
    mtime1 = (tmp_path / "skills" / "repeat_industry" / "skills.yaml").stat().st_mtime

    scaffold("repeat_industry")  # should not overwrite
    mtime2 = (tmp_path / "skills" / "repeat_industry" / "skills.yaml").stat().st_mtime

    assert mtime1 == mtime2
