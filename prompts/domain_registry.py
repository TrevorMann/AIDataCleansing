"""
Domain registry loader. Source of truth: data/domain_registry.json.

Records which domains are initialized, their sub-category dimension (country, platform, etc.),
and where their skills/seeders live. Updated automatically by scripts/domain.py scaffold.
"""

import json
import os
from functools import lru_cache

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "domain_registry.json",
)


@lru_cache(maxsize=1)
def _load() -> dict:
    if os.path.exists(_REGISTRY_PATH):
        with open(_REGISTRY_PATH) as f:
            return json.load(f)
    return {"active_domain": None, "domains": {}}


def get_active_domain() -> str | None:
    return _load().get("active_domain")


def get_domain_config(domain: str | None = None) -> dict:
    reg = _load()
    domain = domain or reg.get("active_domain") or ""
    return reg.get("domains", {}).get(domain, {})


def get_initialized_domains() -> list[str]:
    return list(_load().get("domains", {}).keys())


def register_domain(
    domain: str,
    *,
    label: str = "",
    sub_category_dimension: str = "",
    sub_categories: list | None = None,
    set_active: bool = True,
) -> None:
    """
    Add or update a domain entry in domain_registry.json.
    Called by scripts/domain.py scaffold — safe to call multiple times (idempotent).
    """
    from datetime import date

    if os.path.exists(_REGISTRY_PATH):
        with open(_REGISTRY_PATH) as f:
            reg = json.load(f)
    else:
        reg = {
            "_note": "Tracks initialized domains. Updated by 'scripts/domain.py scaffold'.",
            "active_domain": None,
            "domains": {},
        }

    existing = reg.setdefault("domains", {}).get(domain, {})
    reg["domains"][domain] = {
        "initialized_at": existing.get("initialized_at", str(date.today())),
        "label": label or existing.get("label", f"{domain} data cleaning"),
        "sub_category_dimension": sub_category_dimension or existing.get("sub_category_dimension", ""),
        "sub_categories": sub_categories if sub_categories is not None else existing.get("sub_categories", []),
        "prompt_module": f"prompts.domains.{domain}",
        "skills_path": f"skills/{domain}/skills.yaml",
        "seeders_path": f"seeders/{domain}/manifest.yaml",
    }

    if set_active or reg.get("active_domain") is None:
        reg["active_domain"] = domain

    os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
    with open(_REGISTRY_PATH, "w") as f:
        json.dump(reg, f, indent=2)

    _load.cache_clear()
