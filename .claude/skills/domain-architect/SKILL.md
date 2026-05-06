---
name: domain-architect
description: 
  Researches and blueprints new industry domains. Identifies master data caching needs, 
  entity relationships, and multi-step cleaning escalation paths.
  use when "research [industry] domain", "add architecture for [industry]", 
  "setup [industry] caching strategy", or "define data model for [industry]".
alllowed-tools: [web_search, memory]
---

# Domain Architect & Discovery Agent

The Architect acts as the "Researcher." It finds industry standards and defines the 
"Infrastructure Blueprint" (Postgres SSoT) and "Cleaning Rules" (Thresholds & Paths).

## 1. Research Strategy (Discovery Phase)

For any new industry, you must first establish the "landscape."
**Research Queries:**
- "Standard data model for [industry] [use case] "
- "Look for Authoritative open-source projects, government datasets, or industry consortiums that publish data in this domain."
- "Look for common data formats and non changing data (eg: Zip/Postal codes, Country codes, Address, or other standard information)"
- "[industry] industry standard identifiers (e.g., NPI for Healthcare, ISIN for Finance)"
- "Common data quality issues in [industry] [specific field]"

## 2. Entity & Caching Strategy

Distinguish between **Master Data** (Static/Reference) and **Transactional Data** (Dynamic).

**Logic:**
- **Identify Master Data:** What data should be "Seeded"? (e.g., FSA codes, Ticker lists, city, state/province, country based on zip/postal codes).
- **Propose Cache Tables:** Define `ref_` tables in Postgres to store these lookups locally to reduce LLM/Search costs.
- **Reference Authority:** Identify the "Source of Truth" for lookups (e.g., USPS for addresses, OpenCorporates for legal entities).

## 3. Escalation Path Mapping (Confidence Triggers)

Define which fields follow your 3-step pipeline (Seeded -> Lookup -> Search).

| Tier | Source Type | Confidence Threshold | Typical Fields |
| :--- | :--- | :--- | :--- |
| **Tier 1: Seeded** | Local `ref_` tables | 0.95+ | ISO Country codes, Zip/Postal formats |
| **Tier 2: Lookup** | Known API/LLM Lookup | 0.80 - 0.94 | Municipality mapping, Job Title normalization |
| **Tier 3: Search** | Web Search Escalation | < 0.80 | Niche business names, obscure property details |

## 4. Process Workflow

1. **Perform Research:** Use `web_search` to find industry-standard DDL or entity lists.
2. **Draft Domain Blueprint:**
   - Define Seeding / lookup tables and data elements. 
   - Include "Enhancement Metadata" (`_source_confidence`, `_last_enhanced_at`) in the schema to support your cleaning pipeline.
3. **Generate Industry Rules:**
   - Create/Update `rules/{{industry}}.md` (based on your Industry Pattern Catalog).
   - Define specific regex/format validators for the domain.
   - Define domain-specific data sets used for fuzzy matching or authority lookups (e.g., municipality lists, job title hierarchies, address derivations).
4. **Handoff:** 
   - Call `backend-schema-manager` to generate initial postgres SSoT and subsequent Snowflake/DuckDB/init files.
   - Inform the `data-cleaning` skill of the new rules and cache tables.

## 5. Output Artifacts

- **Blueprint:** `db/blueprints/{{industry}}_blueprint.md` (Research findings).
- **Rules:** `rules/{{industry}}.md` (Logic for the cleaning skill).
- **DDL:** `db/migrations/{{YYYYMMDD}}_init_{{industry}}.sql` (The Postgres SSoT).

---

## Example Action: Real Estate
If asked to "Define Real Estate FSA logic":
1. **Search:** Finds that FSAs map to specific Municipalities but can occasionally overlap.
2. **Cache:** Identifies that a `ref_fsa_municipality` table should be seeded.
3. **Escalation:** 
   - Tier 1: Local lookup in `ref_fsa_municipality`.
   - Tier 2: LLM lookup for "M-series" Toronto codes.
   - Tier 3: Web search for new development areas not in local cache.