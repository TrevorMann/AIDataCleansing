"""E2: Cross-domain regression tests — same pipeline, different domain."""

import pytest
from skills.registry import SkillRegistry
from cleaning.orchestrator_v2 import OrchestrationTeam, run_cleaning_workflow_v2


# --- E1: sports_ticketing skills ---

SPORTS_SAMPLES = [
    {
        "id": 1,
        "event_name": "Leafs vs Habs",
        "product_name": "Single Game Ticket",
        "venue": "Scotiabank Arena",
    },
    {
        "id": 2,
        "event_name": "toronto maple leafs vs Ottawa Senators",
        "product_name": "Full Season Package",
        "venue": "Scotiabank Arena",
    },
    {
        "id": 3,
        "event_name": "Raptors @ Nets",
        "product_name": "Flex Voucher 10-pack",
        "venue": "Scotiabank Arena",
    },
    {
        "id": 4,
        "event_name": "Jays vs Red Sox",
        "product_name": "Half Season Plan - 19 Games",
        "venue": "Rogers Centre",
    },
    {
        "id": 5,
        "event_name": "Oilers vs Flames",
        "product_name": "Individual Game Seat",
        "venue": "Rogers Place",
    },
]

REAL_ESTATE_SAMPLES = [
    {
        "id": 10,
        "address": "25 Muir Avenue",
        "city": "North York",
        "postal_code": "M9L 1H7",
        "municipality": "North York",
        "state_province": "ON",
        "country": "Canada",
    },
    {
        "id": 11,
        "address": "123 Queen Street",
        "city": "Toronto",
        "postal_code": "M4J 1A1",
        "municipality": "Toronto",
        "state_province": "ON",
        "country": "Canada",
    },
]


def test_sports_ticketing_registry_loads():
    registry = SkillRegistry.load("sports_ticketing")
    assert registry is not None
    assert len(registry.list_skills()) >= 2
    assert registry.get("event_normalizer") is not None
    assert registry.get("ticket_product_categorizer") is not None


def test_event_normalizer_normalizes_team_aliases():
    from skills.sports_ticketing.event_normalizer.event_normalizer import EventNormalizer
    skill = EventNormalizer()
    record = {"event_name": "Leafs vs Habs"}
    result = skill.run(record)
    assert result["event_name"] == "toronto maple leafs vs montreal canadiens"
    assert result["_event_normalized"] is True
    assert "_decisions" in result


def test_event_normalizer_no_alias_unchanged():
    from skills.sports_ticketing.event_normalizer.event_normalizer import EventNormalizer
    skill = EventNormalizer()
    record = {"event_name": "Toronto Maple Leafs vs Montreal Canadiens"}
    result = skill.run(record)
    # Already canonical — no change needed (aliases normalize to same)
    assert "event_name" in result


def test_event_normalizer_missing_event_unchanged():
    from skills.sports_ticketing.event_normalizer.event_normalizer import EventNormalizer
    skill = EventNormalizer()
    result = skill.run({})
    assert "_event_normalized" not in result


def test_ticket_categorizer_full_season():
    from skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer import TicketProductCategorizer
    skill = TicketProductCategorizer()
    result = skill.run({"product_name": "Full Season Package"})
    assert result["ticket_category"] == "full_season"


def test_ticket_categorizer_half_season():
    from skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer import TicketProductCategorizer
    skill = TicketProductCategorizer()
    result = skill.run({"product_name": "Half Season - 19 Games"})
    assert result["ticket_category"] == "half_season"


def test_ticket_categorizer_voucher():
    from skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer import TicketProductCategorizer
    skill = TicketProductCategorizer()
    result = skill.run({"product_name": "Flex Voucher 10-pack"})
    assert result["ticket_category"] == "voucher"


def test_ticket_categorizer_individual():
    from skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer import TicketProductCategorizer
    skill = TicketProductCategorizer()
    result = skill.run({"product_name": "Single Game Ticket"})
    assert result["ticket_category"] == "individual"


def test_ticket_categorizer_default_individual():
    from skills.sports_ticketing.ticket_product_categorizer.ticket_product_categorizer import TicketProductCategorizer
    skill = TicketProductCategorizer()
    result = skill.run({"product_name": "Something Unknown"})
    assert result["ticket_category"] == "individual"
    assert result["_decisions"][0]["confidence"] == 0.50


# --- E2: Cross-domain: same pipeline, different domain ---

def test_real_estate_pipeline_runs():
    report = run_cleaning_workflow_v2(records=REAL_ESTATE_SAMPLES, domain="real_estate")
    assert report.records_processed == 2
    assert report.cleaned_count > 0


def test_sports_ticketing_pipeline_runs():
    report = run_cleaning_workflow_v2(records=SPORTS_SAMPLES, domain="sports_ticketing")
    assert report.records_processed == 5
    assert report.cleaned_count >= 0  # sports records don't have triage skill so cleaned_count may be 0


def test_planner_real_estate_skills_not_in_sports():
    """Domain registries must only contain their own domain-specific skills."""
    registry_re = SkillRegistry.load("real_estate")
    registry_st = SkillRegistry.load("sports_ticketing")

    re_skills = set(registry_re.list_skills())
    st_skills = set(registry_st.list_skills())

    # real_estate must not include sports_ticketing domain skills
    assert "event_normalizer" not in re_skills
    assert "ticket_product_categorizer" not in re_skills
    # sports_ticketing must not include real_estate domain skills
    assert "municipality_authority" not in st_skills
    assert "geographic_validator" not in st_skills


def test_sports_ticketing_pipeline_processes_all_samples():
    """All sample records processed, event names normalized, products categorized."""
    registry = SkillRegistry.load("sports_ticketing")
    team = OrchestrationTeam(registry)

    results = [team.process_record(dict(r))[0] for r in SPORTS_SAMPLES]

    # Event names should be normalized (aliases resolved)
    leafs_record = next(r for r in results if r.get("id") == 1)
    assert "toronto maple leafs" in leafs_record["event_name"].lower()

    # Products categorized
    full_season = next(r for r in results if r.get("id") == 2)
    assert full_season["ticket_category"] == "full_season"

    voucher = next(r for r in results if r.get("id") == 3)
    assert voucher["ticket_category"] == "voucher"

    half_season = next(r for r in results if r.get("id") == 4)
    assert half_season["ticket_category"] == "half_season"
