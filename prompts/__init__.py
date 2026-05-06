"""
System prompt assembler.

build_system_prompt(sub, schema, domain) returns a structured prompt:

    <general_rules>          ← BASE_RULES, always loaded
      <schema>...</schema>   ← DB schema injected here
      GENERAL RULES...
    </general_rules>

    <domain_rules domain="X" sub="Y">   ← only when domain match found
      ...domain/sub-specific rules...
    </domain_rules>

XML tags help the model distinguish which rules are universal vs. domain-specific,
and clearly bound where each layer starts and ends.

Layers loaded:
  base     = prompts/base.py               (always)
  domain   = prompts/domains/<domain>/     (from domain_registry active_domain)
  sub      = the sub-category key          (e.g. country code "CA", platform "ticketmaster")

Adding a new domain:
  1. Create prompts/domains/<domain>/__init__.py with get_prompt(sub) and DOMAIN_LABEL
  2. Add sub-category prompt files (e.g. ca.py, usa.py, or general.py)
  3. Register in data/domain_registry.json (done automatically by scripts/domain.py scaffold)
"""

import importlib
from .base import BASE_RULES
from .domain_registry import get_active_domain


def _load_domain_module(domain: str):
    """Import prompts/domains/<domain>/__init__.py. Returns module or None."""
    if not domain:
        return None
    try:
        return importlib.import_module(f"prompts.domains.{domain}")
    except ImportError:
        return None


def build_system_prompt(sub: str | None = None, schema: str = "", domain: str | None = None) -> str:
    """
    Assemble system prompt for the given domain and sub-category.

    Parameters
    ----------
    sub    : Sub-category key — country code (CA/USA/NL/...) for real_estate,
             platform name (ticketmaster/axs) for sports_ticketing, etc.
             Pass None to get domain-level rules only (no sub-category layer).
    schema : DB schema string injected into the base prompt.
    domain : Override active domain. Defaults to data/domain_registry.json active_domain.

    Returns
    -------
    str — structured prompt with XML-tagged layers.
    """
    domain = domain or get_active_domain() or ""
    schema_block = f"\nDatabase schema:\n{schema}\n" if schema else ""
    base = BASE_RULES.format(schema=schema_block)

    mod = _load_domain_module(domain)
    domain_prompt = mod.get_prompt(sub) if mod and hasattr(mod, "get_prompt") else ""

    # Wrap base rules
    parts = [f"<general_rules>\n{base.strip()}\n</general_rules>"]

    # Wrap domain rules — tag carries domain + sub so model knows context
    if domain_prompt.strip():
        sub_attr = f' sub="{sub}"' if sub else ""
        parts.append(
            f'<domain_rules domain="{domain}"{sub_attr}>\n{domain_prompt.strip()}\n</domain_rules>'
        )

    return "\n\n".join(parts)
