# Prompt Evaluation Framework

Standalone evaluation harness for testing prompt effectiveness. Separate from unit tests (`tests/`).

## What This Does

Evaluates how well a prompt (+ schema) handles data cleaning tasks.

**Process:**
1. **Load dataset** — hand-crafted or auto-generated test records with known issues
2. **Call LLM** — sends each record with the prompt; captures full response
3. **Trace reasoning** — logs system prompt, user message, response, token usage
4. **Score response** — compliance (did it follow rules?), clarity (is reasoning transparent?)
5. **Generate report** — HTML showing input → reasoning → output per test case

## Files

| File | Purpose |
|------|---------|
| `dataset_generator.py` | Auto-generate test cases from any prompt |
| `prompt_evaluator.py` | Core harness: load dataset → call LLM → capture reasoning |
| `metrics.py` | Score responses (compliance, clarity, extract cleaned JSON) |
| `report_generator.py` | Render HTML report with per-test-case drill-down |
| `run.py` | CLI entry point (see below) |
| `datasets/general_cleaning.json` | 11 hand-crafted baseline test cases |
| `results/` | Output files (auto-generated, gitignored) |

## Running Evaluations

```bash
# Evaluate baseline dataset
python evals/run.py --dataset general_cleaning --output-html

# Auto-generate test cases from base prompt, then evaluate
python evals/run.py --generate --num-cases 10 --output-html

# Full output (JSON + HTML)
python evals/run.py --dataset general_cleaning --output-json --output-html

# List available datasets
python evals/run.py --list-datasets
```

Output files land in `evals/results/` with timestamp prefix:
```
evals/results/
  2026-05-06-120000-general_cleaning-report.html
  2026-05-06-120000-general_cleaning-results.json   # (with --output-json)
  2026-05-06-120000-general_cleaning-metrics.json   # (with --output-json)
```

Open the `.html` file in any browser to review results.

## Interpreting Results

Each test case card shows:

| Section | What it tells you |
|---------|-------------------|
| **Scores** | Accuracy / Compliance / Clarity as colored % |
| **Expected Behavior** | What the prompt *should* do for this record |
| **Evaluation Criteria** | Specific checkpoints to verify manually |
| **Input Record** | The dirty data sent to the LLM |
| **LLM Response** | Full reasoning (field-by-field analysis + JSON output) |
| **Extracted Cleaned Record** | JSON parsed out of the response |
| **Compliance / Clarity Notes** | ✅ passing / ⚠️ warning per rule |
| **Evaluation Trace** | Step-by-step log: prompt size, model called, token counts |

**Score colors:**
- 🟢 Green (≥ 80%) — Good
- 🟠 Orange (60–79%) — Acceptable
- 🔴 Red (< 60%) — Investigate prompt

> **Note:** Accuracy score is `50%` placeholder — it requires human review of the LLM response against evaluation criteria. Compliance and Clarity are heuristic-scored automatically.

## What the Base Prompt Covers

`prompts/base.py` is tested against:

1. **Country detection** — infer from postal code, city, phone prefix
2. **Spelling errors** — obvious typos in city names
3. **Phone formatting** — country-specific standards
4. **Proper case** — names, cities, countries
5. **Street abbreviations** — expand St→Street, Blvd→Boulevard (without breaking proper nouns)
6. **Data type validation** — non-numeric age, incomplete postal codes
7. **Uncertainty handling** — ambiguous records should be documented, not guessed

## Auto-Generating Test Datasets

For any prompt (base, domain-specific, skill):

```python
from evals.dataset_generator import DatasetGenerator
from prompts import build_system_prompt
from schema_discovery import format_schema_for_prompt

# Load any prompt
schema = format_schema_for_prompt("data/cleaning.db")
prompt = build_system_prompt(sub="CA", schema=schema)  # or any domain/sub

# Generate test cases
gen = DatasetGenerator(prompt, schema=schema, dataset_name="real_estate_ca")
dataset = gen.generate_dataset(num_cases=10)

# Save (can then evaluate with --dataset real_estate_ca)
gen.save_dataset(dataset, "evals/datasets/real_estate_ca.json")
```

Then run: `python evals/run.py --dataset real_estate_ca --output-html`

## Adding Hand-Crafted Test Cases

Edit `evals/datasets/general_cleaning.json`, add to `test_cases` array:

```json
{
  "id": "your_unique_id",
  "description": "One sentence: what this tests",
  "input": {
    "name": "...",
    "city": "...",
    "country": "..."
  },
  "expected_behavior": "What the LLM should do with this record",
  "evaluation_criteria": [
    "Field X should become Y",
    "Field Z should be marked uncertain"
  ]
}
```

## Traceability

Every evaluation logs a `trace` array in the results JSON:

```json
{
  "trace": [
    "Evaluating: ambiguous_country_1 — Toronto, no country",
    "System prompt length: 2847 chars",
    "User message length: 534 chars",
    "Calling anthropic backend with model claude-haiku-4-5-20251001...",
    "Response stop_reason: end_turn",
    "LLM response length: 1203 chars"
  ]
}
```

Use this to understand why an evaluation behaved a certain way — prompt too long? Model changed? Token budget hit?

## Why Separate From `tests/`?

| `tests/` (pytest) | `evals/` (this framework) |
|-------------------|--------------------------|
| Validates code correctness | Validates prompt quality |
| Pass/fail assertions | Scored 0–100% with human review |
| Runs in CI | Run manually on demand |
| Mocked LLM | Real LLM calls (costs tokens) |
| Deterministic | Non-deterministic (LLM output varies) |

## Next Steps

- [ ] Test domain-specific prompts (`prompts/domains/real_estate/ca.py`)
- [ ] Test skill injection (`data-cleaning`, `domain-architect`)
- [ ] Implement field-level accuracy scoring (not just 50% placeholder)
- [ ] Add LLM-as-judge for automated accuracy scoring
- [ ] Compare same test cases across multiple models
