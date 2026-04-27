from .base import BASE_RULES
from .canada import CANADA_RULES
from .usa import USA_RULES
from .netherlands import NETHERLANDS_RULES
from .mexico import MEXICO_RULES
from .japan import JAPAN_RULES

_COUNTRY_RULES = {
    'CA': CANADA_RULES,
    'USA': USA_RULES,
    'NL': NETHERLANDS_RULES,
    'MX': MEXICO_RULES,
    'JP': JAPAN_RULES,
}


def build_system_prompt(country_scope: str, schema: str = "") -> str:
    """Assemble the system prompt for the given country scope."""
    base = BASE_RULES.format(schema=schema)
    return base + _COUNTRY_RULES.get(country_scope, "")
