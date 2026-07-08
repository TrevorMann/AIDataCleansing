"""Annotation prompt builder for column metadata discovery.

This module provides build_annotation_prompt() to generate an LLM prompt
for annotating raw database columns with metadata: description, type hints,
and confidence scoring for ambiguous column names.
"""


def build_annotation_prompt(
    domain: str,
    domain_description: str,
    table_name: str,
    column_name: str,
    sample_values: list,
) -> str:
    """Build a prompt for LLM column annotation.

    Args:
        domain: Domain key (e.g., "real_estate", "sports_ticketing")
        domain_description: Human-readable domain context
        table_name: Raw data table name
        column_name: Column to annotate
        sample_values: List of sample values from the column (may be empty)

    Returns:
        Formatted prompt string for LLM call. Expects JSON response with
        {"description": "...", "confidence": 0.0-1.0}
    """
    samples_str = str(sample_values) if sample_values else "none available"
    return (
        f"Domain: {domain}\n"
        f"Domain context: {domain_description}\n"
        f"Table: {table_name}\n"
        f"Column: {column_name}\n"
        f"Sample values (may be empty): {samples_str}\n\n"
        "Respond with JSON only:\n"
        '{"description": "<one sentence: what this column stores, expected format, any known constraints>",'
        ' "confidence": <0.0-1.0 float>}\n\n'
        "Rules:\n"
        "- description must be ≤ 120 characters\n"
        "- Use domain context to resolve ambiguous column names"
        " (e.g. 'price' means listing price in real estate)\n"
        "- confidence < 0.70 means the column name/samples remain ambiguous"
        " even with domain context\n"
        "- Do not hallucinate constraints not evident from name, samples, or domain context\n"
    )


def build_table_annotation_prompt(
    domain: str,
    domain_description: str,
    table_name: str,
    columns: list,
) -> str:
    """Build a prompt annotating ALL columns of a table in one call.

    Sibling columns give the LLM context a lone column name can't (e.g.
    'home_team' next to 'venue_name' and 'event_date' disambiguates a sports
    schema). Expects JSON: {"table_description": str,
    "columns": [{"column_name", "description", "confidence"}]}.

    Args:
        columns: [{"name": str, "samples": list}] for every column to annotate
    """
    col_lines = []
    for col in columns:
        samples = col.get("samples") or []
        samples_str = str(samples[:5]) if samples else "none available"
        col_lines.append(f"  {col['name']}  (samples: {samples_str})")
    columns_block = "\n".join(col_lines)

    return (
        f"Domain: {domain}\n"
        f"Domain context: {domain_description}\n"
        f"Table: {table_name}\n"
        f"Columns:\n{columns_block}\n\n"
        "Annotate this table for downstream AI/LLM consumers of the database.\n"
        "Respond with JSON only:\n"
        "{\n"
        '  "table_description": "<1-2 sentences: what one row of this table represents>",\n'
        '  "columns": [\n'
        '    {"column_name": "<exact name from above>",\n'
        '     "description": "<what it stores, expected format/units, value domain if enumerable, join-key role if any>",\n'
        '     "confidence": <0.0-1.0 float>},\n'
        "    ...one entry per column listed above...\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- each description must be ≤ 300 characters\n"
        "- use the sibling columns and domain context to resolve ambiguous names\n"
        "- confidence < 0.70 means the column remains ambiguous even in context\n"
        "- do not hallucinate constraints not evident from names, samples, or domain context\n"
    )
