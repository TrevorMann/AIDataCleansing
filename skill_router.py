"""
Skill router: detects user intent and injects the matching SKILL.md body into
the system prompt so the model follows the skill's workflow.

Skills live in .claude/skills/<name>/SKILL.md.
Frontmatter (--- ... ---) is stripped before injection.

Validates skill format:
  - Must have --- frontmatter ---
  - Must have body content after frontmatter
  - Optional: size check (warn if > 50KB)
"""

import os
import re
import logging

logger = logging.getLogger(__name__)

_SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "skills")
_MAX_SKILL_SIZE = 50 * 1024  # 50KB warning threshold

# Maps skill directory name → trigger phrases (all lowercased substrings)
SKILL_TRIGGERS: dict[str, list[str]] = {
    "domain-architect": [
        "research",
        "architect",
        "blueprint",
        "add architecture",
        "setup caching",
        "define data model",
        "new domain",
        "industry domain",
    ],
    "backend-schema-manager": [
        "migrate schema",
        "add backend",
        "snowflake",
        "duckdb",
        "bigquery",
        "redshift",
        "sql server",
        "new backend",
    ],
    "data-cleaning": [
        "build pipeline",
        "fuzzy match",
        "entity resolution",
        "field normalization",
        "cleaning pipeline",
        "triage routing",
        "data quality",
        "confidence score",
    ],
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_frontmatter(content: str) -> str:
    """Strip frontmatter and return body. Validates format."""
    if not content.startswith("---"):
        raise ValueError(f"Skill must start with '---' frontmatter, got: {content[:30]}")
    body = _FRONTMATTER_RE.sub("", content, count=1).strip()
    if not body:
        raise ValueError("Skill has no body content after frontmatter")
    return body


def detect_skill(user_input: str) -> str | None:
    """Return the first matching skill name for user_input, or None."""
    lower = user_input.lower()
    for skill, triggers in SKILL_TRIGGERS.items():
        if any(t in lower for t in triggers):
            return skill
    return None


def load_skill(skill_name: str) -> str:
    """
    Load SKILL.md body (frontmatter stripped) for skill_name.

    Returns '' if not found. Validates format and logs warnings on issues.
    Logs size if > 50KB (may impact token budget).
    """
    path = os.path.join(_SKILL_DIR, skill_name, "SKILL.md")
    if not os.path.exists(path):
        logger.debug(f"Skill not found: {skill_name} at {path}")
        return ""

    try:
        with open(path) as f:
            content = f.read()

        # Size warning
        if len(content) > _MAX_SKILL_SIZE:
            logger.warning(
                f"Skill '{skill_name}' is {len(content):,} bytes (> {_MAX_SKILL_SIZE:,}). "
                "Consider splitting into smaller skills."
            )

        body = _strip_frontmatter(content)
        logger.debug(f"Loaded skill '{skill_name}' ({len(body):,} bytes)")
        return body

    except ValueError as e:
        logger.error(f"Skill format error in '{skill_name}': {e}")
        return ""
    except Exception as e:
        logger.error(f"Failed to load skill '{skill_name}': {e}")
        return ""


def inject_skill(base_system: str, skill_name: str) -> str:
    """
    Append skill instructions to base_system. Returns augmented system string.

    Validates that skill loads successfully before appending.
    """
    body = load_skill(skill_name)
    if not body:
        logger.warning(f"Skill injection skipped (empty): {skill_name}")
        return base_system
    return f"{base_system}\n\n# Active Skill: {skill_name}\n\n{body}"
