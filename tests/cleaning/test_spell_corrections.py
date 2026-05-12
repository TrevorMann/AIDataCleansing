"""Tests for spell corrections DB loader (B3)."""

import csv
import inspect
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import skills.real_estate.spell_checker.spell_checker as spell_checker_module
from cleaning.spell_corrections_data import load_seed_corrections, get_corrections_dict
from skills.real_estate.spell_checker.spell_checker import SpellChecker


# --- Lock test: no hardcoded corrections in source ---

def test_no_hardcoded_corrections_in_spell_checker_source():
    """Lock: hardcoded misspellings must NOT appear in SpellChecker source."""
    import skills._common.spell_checker.spell_checker as spell_checker_module
    src = inspect.getsource(spell_checker_module)
    assert "scarbbrough" not in src, "Hardcoded 'scarbbrough' found in spell_checker.py"
    assert "toronot" not in src, "Hardcoded 'toronot' found in spell_checker.py"
    assert "etobicoe" not in src, "Hardcoded 'etobicoe' found in spell_checker.py"


# --- load_seed_corrections ---

def _make_csv(rows, tmp_path):
    p = tmp_path / "corrections.csv"
    with open(p, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["wrong", "right", "source", "confidence"])
        writer.writeheader()
        writer.writerows(rows)
    return str(p)


def test_load_seed_corrections_inserts_rows(tmp_path):
    seed = _make_csv([
        {"wrong": "toronot", "right": "toronto", "source": "manual_seed", "confidence": "1.0"},
        {"wrong": "scarbbrough", "right": "scarborough", "source": "manual_seed", "confidence": "1.0"},
    ], tmp_path)

    inserted = []
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.executemany = lambda sql, rows: inserted.extend(rows)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    count = load_seed_corrections(mock_conn, seed, "real_estate")

    assert count == 2
    assert any(r[0] == "toronot" and r[2] == "toronto" for r in inserted)
    assert any(r[0] == "scarbbrough" and r[2] == "scarborough" for r in inserted)
    mock_conn.commit.assert_called_once()


def test_load_seed_corrections_missing_file():
    mock_conn = MagicMock()
    with pytest.raises(FileNotFoundError):
        load_seed_corrections(mock_conn, "/nonexistent/path.csv", "real_estate")


# --- get_corrections_dict ---

def test_get_corrections_dict_returns_mapping():
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = [("toronot", "toronto"), ("scarbbrough", "scarborough")]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    result = get_corrections_dict(mock_conn, "real_estate")

    assert result == {"toronot": "toronto", "scarbbrough": "scarborough"}
    mock_cur.execute.assert_called_once()
    assert "real_estate" in mock_cur.execute.call_args[0][1]


# --- SpellChecker integration ---

def test_spell_checker_no_conn_empty_corrections():
    """No pg_conn → SpellChecker._overrides is empty dict."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": []})
    assert sc._overrides == {}


def test_spell_checker_db_error_falls_back_to_empty():
    """DB error during load → _overrides empty, no exception raised."""
    with patch("cleaning.spell_corrections_data.get_corrections_dict", side_effect=Exception("DB down")):
        mock_conn = MagicMock()
        sc = SpellChecker({"pg_conn": mock_conn, "threshold": 0.85, "text_fields": []})
    assert sc._overrides == {}


def test_spell_checker_with_db_corrections():
    """With DB corrections injected, SpellChecker corrects misspellings in text_fields."""
    corrections = {"toronot": "toronto", "scarbbrough": "scarborough"}
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=corrections):
        mock_conn = MagicMock()
        sc = SpellChecker({
            "pg_conn": mock_conn,
            "threshold": 0.85,
            "text_fields": ["city", "municipality"],
        })

    record = {"city": "toronot", "municipality": "scarbbrough", "address": "123 Main St"}
    result = sc.run(record)

    assert result["city"] == "toronto"
    assert result["municipality"] == "scarborough"
    assert result["address"] == "123 Main St"   # not in text_fields — untouched
    assert "_decisions" not in result            # audit is on skill instance now
    audit = sc.get_audit()
    assert len(audit) == 2


def test_spell_checker_uses_symspellpy_for_obvious_typo():
    """symspellpy catches 'toronot' without any DB corrections loaded."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": ["city"]})
    result = sc.run({"city": "toronot"})
    assert result["city"] == "toronto"


def test_spell_checker_only_touches_text_fields():
    """Fields not in text_fields must not be modified."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": ["city"]})
    result = sc.run({"city": "toronot", "last_name": "Smyth"})
    assert result["city"] == "toronto"
    assert result["last_name"] == "Smyth"  # untouched


def test_spell_checker_no_text_fields_config_touches_nothing():
    """Empty text_fields → nothing processed."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": []})
    result = sc.run({"city": "toronot"})
    assert result["city"] == "toronot"


def test_spell_checker_override_takes_priority():
    """Domain override table beats symspellpy — exact match wins at confidence=1.0."""
    overrides = {"scarbbrough": "scarborough"}
    with patch("cleaning.spell_corrections_data.get_corrections_dict", return_value=overrides):
        sc = SpellChecker({"pg_conn": MagicMock(), "threshold": 0.85, "text_fields": ["municipality"]})
    result = sc.run({"municipality": "scarbbrough"})
    assert result["municipality"] == "scarborough"
    audit = sc.get_audit()
    assert any("override" in e["reason"] for e in audit)


def test_spell_checker_audit_not_in_record():
    """_decisions must NOT appear in the returned record."""
    sc = SpellChecker({"threshold": 0.85, "text_fields": ["city"]})
    result = sc.run({"city": "toronot"})
    assert "_decisions" not in result
