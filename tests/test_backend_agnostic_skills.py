"""Lock test: skills must not bind to a specific DB driver.

Skills are domain logic and must stay backend-agnostic. DB access goes through the
db/schema_discovery.py dispatcher or a conn-based helper in db/ (e.g.
db.pg_query_memory) that the dispatcher delegates to — never a direct psycopg /
sqlite3 import inside a skill.

Background: the gap-type vocabulary review (2026-06-15) found a reader being added
that bypassed the dispatcher. The rule was prose in CLAUDE.md; this makes it fail CI.
See [[feedback_ground_plan_references]] and the backend-agnostic dispatch memory.
"""
import pathlib
import re

SKILLS_DIR = pathlib.Path(__file__).resolve().parent.parent / "skills"

# Direct DB-driver imports that must not appear in skill source.
FORBIDDEN = re.compile(
    r"^\s*(?:import\s+(?:psycopg2?|sqlite3)\b"
    r"|from\s+(?:psycopg2?|sqlite3)\b)",
    re.MULTILINE,
)


def _skill_source_files():
    for path in SKILLS_DIR.rglob("*.py"):
        # Skip caches; skills/ has no test files, but be explicit.
        if "__pycache__" in path.parts:
            continue
        yield path


def test_skills_do_not_import_db_drivers_directly():
    offenders = []
    for path in _skill_source_files():
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN.search(text):
            offenders.append(str(path.relative_to(SKILLS_DIR.parent)))

    assert not offenders, (
        "Skills must not import psycopg/sqlite3 directly — route DB access through "
        "db/schema_discovery.py or a conn-based db/ helper the dispatcher delegates "
        "to. Offending files:\n  " + "\n  ".join(offenders)
    )
