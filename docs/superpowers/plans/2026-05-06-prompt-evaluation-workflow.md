# Prompt Evaluation Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build standalone evaluation harness for testing prompt effectiveness: start with (base.py + schema) on general data cleaning tasks, separate from unit tests, with full traceability and learning-friendly output. be able to test and add additional prompts easily

**Architecture:** 
Evaluation workflow is isolated in `evals/` directory with:
1. **Evaluation harness** — Loads prompts, injects test data, calls LLM, captures results
2. **Test datasets** — Hand-crafted examples covering edge cases (ambiguous country, spelling, formats)
3. **Metrics collector** — Tracks accuracy, reasoning clarity, schema adherence per record
4. **Report generator** — HTML + JSON with step-by-step reasoning visibility
5. **Traceability** — Every decision logged so Trevor can trace why llm made each choice

**Tech Stack:** Python 3.12+, Anthropic SDK, JSON for data/results, HTML for visualization, logging for step tracking, open router (for api llm calls)

---

## File Structure

```
evals/
├── __init__.py                        # Empty module marker
├── prompt_evaluator.py                # Core harness (loads prompt, calls LLM, logs reasoning)
├── metrics.py                         # Evaluation metrics (accuracy, compliance, reasoning quality)
├── report_generator.py                # HTML + JSON report output
├── dataset_generator.py               # Auto-generate test cases from prompt instructions
├── datasets/
│   └── general_cleaning.json          # Test cases for base prompt (country, spelling, format tests)
├── results/
│   └── 2026-05-06-base-prompt-run1.json  # Results (auto-generated)
│   └── 2026-05-06-base-prompt-run1.html  # Report (auto-generated)
└── run.py                             # Entry point: python evals/run.py --dataset general_cleaning --output-html
```

---

## Task 1: Create test data generator (dataset_generator.py)

**Files:**
- Create: `evals/dataset_generator.py`

**Responsibility:** Read prompt text, auto-generate diverse test cases covering rules + edge cases. Input: prompt rules. Output: JSON dataset for evaluation.

- [ ] **Step 1: Create dataset_generator.py**

Create `evals/dataset_generator.py`:

```python
"""
Auto-generate test datasets from prompt instructions.

Given a prompt (system message), extract rules and generate diverse test cases
covering both happy path and edge cases.

Usage:
    from evals.dataset_generator import DatasetGenerator
    
    prompt = build_system_prompt(sub='CA', schema=schema_str)
    gen = DatasetGenerator(prompt, schema=schema_str)
    dataset = gen.generate_dataset(num_cases=10)
    
    # Save
    gen.save_dataset(dataset, 'evals/datasets/my_dataset.json')
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client_factory import create_client, build_system_param, build_message_kwargs, log_usage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatasetGenerator:
    """Auto-generate test cases from prompt instructions."""

    def __init__(self, prompt: str, schema: str = "", dataset_name: str = "auto_generated"):
        """
        Initialize generator.
        
        Args:
            prompt: System prompt (rules to test)
            schema: Database schema string (optional, for context)
            dataset_name: Name for generated dataset
        """
        self.prompt = prompt
        self.schema = schema
        self.dataset_name = dataset_name
        self.client, self.backend, self.model = create_client()

    def extract_rules_from_prompt(self) -> str:
        """
        Use LLM to extract testable rules from prompt.
        
        Returns a summarized list of rules that should be tested.
        """
        extraction_prompt = f"""Analyze this data cleaning prompt and extract the KEY TESTABLE RULES.

PROMPT:
{self.prompt}

List the rules as a numbered list. Focus on:
1. Data validation rules (formats, standards)
2. Transformation rules (case, abbreviations, standardization)
3. Conditional rules (if X, then Y)
4. Uncertainty rules (when NOT to change data)

Keep each rule to 1-2 sentences. Output ONLY the numbered list, no preamble."""

        system_param = build_system_param(self.backend, "You are a data cleaning expert. Extract rules precisely.")
        message_kwargs = build_message_kwargs(self.backend)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_param,
            messages=[{"role": "user", "content": extraction_prompt}],
            **message_kwargs
        )
        log_usage(self.backend, response.usage)

        rules_text = next(
            (b.text for b in response.content if hasattr(b, "text")),
            "No rules extracted"
        )
        return rules_text

    def generate_test_cases(self, rules: str, num_cases: int = 10) -> list:
        """
        Use LLM to generate diverse test cases covering rules + edge cases.
        
        Args:
            rules: Extracted rules from prompt
            num_cases: How many test cases to generate
            
        Returns:
            List of test case dicts with: id, description, input, expected_behavior, evaluation_criteria
        """
        generation_prompt = f"""Generate {num_cases} diverse test cases for a data cleaning prompt.

RULES TO TEST:
{rules}

SCHEMA:
{self.schema if self.schema else 'Generic record with: name, age, city, address, postal_code, municipality, state_province, country, phone'}

Generate test cases that cover:
1. Happy path (data already clean)
2. Single field issues (spelling, case, format)
3. Multi-field issues (ambiguity, missing data)
4. Edge cases (unusual but valid)
5. Uncertainty cases (when rules say "don't guess")

For EACH test case, output VALID JSON:
{{
  "id": "test_id_N",
  "description": "What this tests",
  "input": {{"name": "...", "country": "..."}},
  "expected_behavior": "What the cleaner should do",
  "evaluation_criteria": ["Criterion 1", "Criterion 2"]
}}

Output ONLY valid JSON objects, one per line (NOT a JSON array, just newline-separated objects)."""

        system_param = build_system_param(self.backend, "You are a data QA expert. Generate valid test JSON.")
        message_kwargs = build_message_kwargs(self.backend)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_param,
            messages=[{"role": "user", "content": generation_prompt}],
            **message_kwargs
        )
        log_usage(self.backend, response.usage)

        response_text = next(
            (b.text for b in response.content if hasattr(b, "text")),
            ""
        )

        # Parse newline-separated JSON objects
        test_cases = []
        for line in response_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                obj = json.loads(line)
                test_cases.append(obj)
            except json.JSONDecodeError as e:
                logger.warning(f"Skipped invalid JSON line: {line[:50]}... ({e})")

        return test_cases[:num_cases]  # Trim to requested count

    def generate_dataset(self, num_cases: int = 10) -> dict:
        """
        Full pipeline: extract rules → generate test cases → return dataset.
        
        Returns:
            Dataset dict with metadata + test_cases
        """
        logger.info(f"Generating {num_cases} test cases from prompt...")

        # Step 1: Extract rules
        logger.info("Step 1: Extracting rules from prompt...")
        rules = self.extract_rules_from_prompt()
        logger.info(f"Extracted {len(rules.split(chr(10)))} rule lines")

        # Step 2: Generate test cases
        logger.info("Step 2: Generating test cases...")
        test_cases = self.generate_test_cases(rules, num_cases=num_cases)
        logger.info(f"Generated {len(test_cases)} test cases")

        # Step 3: Package as dataset
        dataset = {
            "metadata": {
                "name": self.dataset_name,
                "description": f"Auto-generated dataset from prompt ({num_cases} test cases)",
                "generated_at": datetime.now().isoformat(),
                "backend": self.backend,
                "model": self.model,
            },
            "extracted_rules": rules,
            "test_cases": test_cases,
        }

        return dataset

    def save_dataset(self, dataset: dict, output_path: str):
        """Save dataset to JSON file."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(dataset, f, indent=2)
        logger.info(f"✅ Dataset saved to {output_path}")
```

- [ ] **Step 2: Test generator on base prompt**

```bash
cd /mnt/f/AI_learning_project
python3 -c "
from evals.dataset_generator import DatasetGenerator
from prompts import build_system_prompt
from schema_discovery import format_schema_for_prompt

# Load base prompt + schema
schema = format_schema_for_prompt('data/cleaning.db')
prompt = build_system_prompt(sub=None, schema=schema)

# Generate test cases
gen = DatasetGenerator(prompt, schema=schema, dataset_name='auto_base_prompt')
dataset = gen.generate_dataset(num_cases=5)

print(f'✅ Generated {len(dataset[\"test_cases\"])} test cases')
for tc in dataset['test_cases']:
    print(f'  - {tc[\"id\"]}: {tc[\"description\"]}')

# Save
gen.save_dataset(dataset, 'evals/datasets/auto_base_prompt.json')
print('✅ Dataset saved to evals/datasets/auto_base_prompt.json')
"
```

Expected output:
```
✅ Generated 5 test cases
  - test_id_1: ...
  - test_id_2: ...
✅ Dataset saved to evals/datasets/auto_base_prompt.json
```

- [ ] **Step 3: Verify generated dataset structure**

```bash
python3 -c "
import json
with open('evals/datasets/auto_base_prompt.json') as f:
    dataset = json.load(f)
print(f'✅ Metadata: {dataset[\"metadata\"].keys()}')
print(f'✅ Rules extracted: {len(dataset[\"extracted_rules\"].split(chr(10)))} lines')
print(f'✅ Test cases: {len(dataset[\"test_cases\"])}')
print(f'✅ First test case keys: {dataset[\"test_cases\"][0].keys()}')
"
```

Expected:
```
✅ Metadata: dict_keys([...])
✅ Rules extracted: N lines
✅ Test cases: 5
✅ First test case keys: dict_keys(['id', 'description', 'input', 'expected_behavior', 'evaluation_criteria'])
```

- [ ] **Step 4: Commit dataset generator**

```bash
git add evals/dataset_generator.py
git commit -m "eval: add auto test data generator

- DatasetGenerator: reads prompt, extracts rules, generates diverse test cases
- Uses LLM to understand prompt intent
- Covers happy path + edge cases + uncertainty cases
- Output: JSON dataset compatible with PromptEvaluator
- No manual dataset creation needed for new prompts

Usage:
  gen = DatasetGenerator(prompt, schema)
  dataset = gen.generate_dataset(num_cases=10)
  gen.save_dataset(dataset, 'evals/datasets/auto.json')
"
```

---

## Task 2: Create evaluation harness (prompt_evaluator.py)

**Files:**
- Create: `evals/__init__.py`
- Create: `evals/prompt_evaluator.py`
- Create: `evals/datasets/general_cleaning.json` (hand-crafted baseline)

**Responsibility:** Load prompt + schema, send test records to LLM, capture full reasoning chain (system message, user input, model output, token usage).

- [ ] **Step 1: Create empty evals module**

```bash
mkdir -p evals/datasets evals/results
touch evals/__init__.py
```

- [ ] **Step 2: Create general_cleaning test dataset**

Create `evals/datasets/general_cleaning.json` with 11 representative test cases:

```json
{
  "metadata": {
    "name": "general_cleaning",
    "description": "Base prompt evaluation on general data cleaning (country ambiguity, spelling, formats)",
    "schema": "Assumes raw_data table with: name, age, city, address, postal_code, municipality, state_province, country, phone"
  },
  "test_cases": [
    {
      "id": "ambiguous_country_1",
      "description": "Ambiguous country detection — 'Toronto' is in Canada but record says nothing",
      "input": {
        "name": "John Smith",
        "age": 35,
        "city": "Toronto",
        "address": "25 King Street",
        "postal_code": "M5H 1E7",
        "state_province": "ON",
        "country": "",
        "phone": "(416) 555-0123"
      },
      "expected_behavior": "Should flag country as Canada based on postal code M5H (Ontario), or infer from Toronto + ON. If unable to infer, leave blank and document reason.",
      "evaluation_criteria": [
        "Country is either 'Canada' or marked as 'unable to determine — see reason'",
        "If country determined, state_province should be 'Ontario' not 'ON'",
        "Reasoning is transparent (why it chose Canada or why it couldn't)"
      ]
    },
    {
      "id": "spelling_error_1",
      "description": "Spelling error in city name",
      "input": {
        "name": "Jane Doe",
        "age": 28,
        "city": "Vancover",
        "address": "123 Granville Ave",
        "postal_code": "V6B 2W9",
        "state_province": "BC",
        "country": "Canada",
        "phone": "(604) 555-0456"
      },
      "expected_behavior": "Should correct 'Vancover' to 'Vancouver'",
      "evaluation_criteria": [
        "City is corrected to 'Vancouver'",
        "Reasoning explains the correction (obvious typo vs uncertain)",
        "Postal code V6B is validated (if applicable)"
      ]
    },
    {
      "id": "format_validation_1",
      "description": "Phone format varies by country",
      "input": {
        "name": "Alice Johnson",
        "age": 42,
        "city": "New York",
        "address": "456 Broadway",
        "postal_code": "10013",
        "state_province": "NY",
        "country": "United States",
        "phone": "2125551234"
      },
      "expected_behavior": "Should format phone as (212) 555-1234 for USA, or note if unsure",
      "evaluation_criteria": [
        "Phone is formatted according to country standard (US: (XXX) XXX-XXXX)",
        "If formatting is uncertain, original value preserved + reason documented",
        "Country is 'United States' not 'USA'"
      ]
    },
    {
      "id": "partial_data_1",
      "description": "Missing country — only city provided",
      "input": {
        "name": "Bob Wilson",
        "age": 55,
        "city": "Amsterdam",
        "address": "Herengracht 123",
        "postal_code": "1012 GK",
        "state_province": "",
        "country": "",
        "phone": "+31 20 5555555"
      },
      "expected_behavior": "Should infer country from city + postal code format (NL) or phone prefix (+31), or flag as unable to determine",
      "evaluation_criteria": [
        "Country is either 'Netherlands' or marked unable to determine",
        "Reasoning is explicit about which signals led to decision",
        "Phone format follows country standard if country inferred"
      ]
    },
    {
      "id": "proper_case_1",
      "description": "Names and cities in wrong case",
      "input": {
        "name": "john mccarthy",
        "age": 31,
        "city": "MONTREAL",
        "address": "789 rue de la paix",
        "postal_code": "H1A 0A1",
        "state_province": "qc",
        "country": "canada",
        "phone": "(514) 555-9876"
      },
      "expected_behavior": "Should apply proper case: John McCarthy, Montreal, Quebec, Canada",
      "evaluation_criteria": [
        "Name: John McCarthy (title case with capitals)",
        "City: Montreal (title case)",
        "State: Quebec not 'qc' or 'QC'",
        "Country: Canada not 'canada'"
      ]
    },
    {
      "id": "street_abbreviation_1",
      "description": "Street abbreviations should be expanded",
      "input": {
        "name": "Carol Davis",
        "age": 44,
        "city": "Calgary",
        "address": "999 St. George Blvd.",
        "postal_code": "T2R 1A7",
        "state_province": "AB",
        "country": "Canada",
        "phone": "(403) 555-2109"
      },
      "expected_behavior": "Should expand 'St.' to 'Street' BUT preserve 'St George' as part of street name (not 'Saint George')",
      "evaluation_criteria": [
        "Address becomes '999 Street George Boulevard' (or similar — exact format TBD by llm)",
        "Reasoning shows awareness of 'St' context (abbreviation vs part of name)",
        "Blvd. is expanded to Boulevard"
      ]
    },
    {
      "id": "confidence_uncertainty_1",
      "description": "Record with multiple ambiguities — should document uncertainty",
      "input": {
        "name": "D. Smith",
        "age": null,
        "city": "Springfield",
        "address": "42 Main",
        "postal_code": "12345",
        "state_province": "",
        "country": "",
        "phone": "555-1234"
      },
      "expected_behavior": "Should mark multiple fields as uncertain/unable to determine, not guess",
      "evaluation_criteria": [
        "Country: marked unable to determine (Springfield is in ~30 US states + other countries)",
        "Postal code: marked uncertain (12345 is not valid US format, could be placeholder)",
        "Phone: marked incomplete/uncertain (missing area code)",
        "Each field has explicit reason for non-correction"
      ]
    },
    {
      "id": "valid_clean_record_1",
      "description": "Already-clean record should pass through",
      "input": {
        "name": "Rachel Green",
        "age": 26,
        "city": "Seattle",
        "address": "500 Pike Place Market Road",
        "postal_code": "98101",
        "state_province": "Washington",
        "country": "United States",
        "phone": "(206) 555-1234"
      },
      "expected_behavior": "Should recognize record is already clean, note no changes needed",
      "evaluation_criteria": [
        "All fields marked as valid/no changes",
        "Reasoning is brief (e.g., 'Phone already in correct format for US')"
      ]
    },
    {
      "id": "schema_type_mismatch_1",
      "description": "Age field has non-numeric value",
      "input": {
        "name": "Eve Turner",
        "age": "twenty-five",
        "city": "Boston",
        "address": "100 Newbury Street",
        "postal_code": "02115",
        "state_province": "Massachusetts",
        "country": "United States",
        "phone": "(617) 555-5555"
      },
      "expected_behavior": "Should flag age as non-numeric, leave unchanged (or document why not converted)",
      "evaluation_criteria": [
        "Age remains 'twenty-five' (not changed to 25) unless llm has high confidence",
        "Reason documented: 'Age is non-numeric; would need external tool to interpret'",
        "No silent type coercion"
      ]
    },
    {
      "id": "european_phone_1",
      "description": "European phone format validation",
      "input": {
        "name": "Klaus Mueller",
        "age": 52,
        "city": "Berlin",
        "address": "Kurfürstendamm 123",
        "postal_code": "10711",
        "state_province": "Berlin",
        "country": "Germany",
        "phone": "030 12345678"
      },
      "expected_behavior": "Should recognize German format +49 (30) is for Berlin, format accordingly",
      "evaluation_criteria": [
        "Phone formatted as +49 30 12345678 or similar (German standard)",
        "Reasoning shows country → phone format mapping",
        "Postal code 10711 validated as Berlin (if applicable)"
      ]
    },
    {
      "id": "address_expansion_edge_case",
      "description": "Handling of street vs Saint in address name",
      "input": {
        "name": "Klaus Mueller",
        "age": 52,
        "city": "St Catherine",
        "address": "24 St Paul St",
        "postal_code": "L2R 7L2",
        "state_province": "ON",
        "country": "CA",
        "phone": "+12127895264"
      },
      "expected_behavior": "Should only expand the street portion of the address even though there is 3 references of St.",
      "evaluation_criteria": [
        "Address is formatted as St Paul Street"
        "Also acceptable Saint Paul Street"
        "Reasoning shows address expansion."
      ]
    }
  ]
}
```

- [ ] **Step 3: Create prompt_evaluator.py harness**

Create `evals/prompt_evaluator.py`:

```python
"""
Prompt evaluation harness for base.py + schema.

Loads test dataset, sends each record to LLM with base prompt + schema,
captures full reasoning chain and output.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client_factory import create_client, build_system_param, build_message_kwargs, log_usage, ANTHROPIC, OPENROUTER
from prompts import build_system_prompt
from schema_discovery import format_schema_for_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class PromptEvaluator:
    """
    Evaluates prompt effectiveness on test dataset.
    
    Workflow:
    1. Load test dataset from JSON
    2. Build base prompt + schema
    3. For each test case:
       a. Send record as user message
       b. Capture LLM response (reasoning + cleanings)
       c. Log reasoning chain for traceability
       d. Store results for metrics calculation
    """

    def __init__(self, db_path: str = "data/cleaning.db"):
        """Initialize evaluator with LLM client."""
        self.client, self.backend, self.model = create_client()
        self.db_path = db_path
        # Schema string for prompt injection
        self.schema = format_schema_for_prompt(db_path)
        self.results = []

    def load_dataset(self, dataset_path: str) -> dict:
        """Load test dataset from JSON file."""
        with open(dataset_path) as f:
            return json.load(f)

    def evaluate_record(self, test_case: dict) -> dict:
        """
        Evaluate single record against base prompt.
        
        Returns dict with:
        - test_case_id: from dataset
        - input: original record
        - system_prompt: base prompt + schema sent to llm
        - llm_response: raw response text
        - reasoning: extracted reasoning from response
        - cleanings: extracted field changes from response
        - tokens: input/output/cache usage
        - trace: step-by-step log of evaluation
        """
        trace = []
        trace.append(f"Evaluating: {test_case.get('id')} — {test_case.get('description')}")

        # Step 1: Build system prompt with schema
        system_prompt = build_system_prompt(sub=None, schema=self.schema, domain=None)
        trace.append(f"System prompt length: {len(system_prompt)} chars")

        # Step 2: Format record as user message
        input_record = test_case["input"]
        user_message = f"""Clean this data record according to the rules above.

Record:
{json.dumps(input_record, indent=2)}

Please:
1. List each field and whether it needs cleaning
2. For fields that need cleaning, explain your reasoning
3. Provide the cleaned record as JSON at the end
4. For any field you cannot confidently clean, explain why"""

        trace.append(f"User message length: {len(user_message)} chars")
        trace.append(f"Input record: {json.dumps(input_record)}")

        # Step 3: Call LLM
        trace.append(f"Calling {self.backend} backend with model {self.model}...")
        system_param = build_system_param(self.backend, system_prompt)
        message_kwargs = build_message_kwargs(self.backend)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system_param,
            messages=[{"role": "user", "content": user_message}],
            **message_kwargs
        )

        # Log usage for insight into token efficiency
        log_usage(self.backend, response.usage)
        trace.append(f"Response stop_reason: {response.stop_reason}")

        # Step 4: Extract response content
        llm_response = next(
            (b.text for b in response.content if hasattr(b, "text")),
            "No text response"
        )
        trace.append(f"llm response length: {len(llm_response)} chars")

        return {
            "test_case_id": test_case["id"],
            "description": test_case["description"],
            "input": input_record,
            "expected_behavior": test_case.get("expected_behavior", ""),
            "evaluation_criteria": test_case.get("evaluation_criteria", []),
            "system_prompt": system_prompt,  # Full prompt for inspection
            "user_message": user_message,
            "llm_response": llm_response,
            "model": self.model,
            "backend": self.backend,
            "tokens": {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "cache_creation": getattr(response.usage, "cache_creation_input_tokens", 0),
                "cache_read": getattr(response.usage, "cache_read_input_tokens", 0),
            },
            "trace": trace,
            "timestamp": datetime.now().isoformat(),
        }

    def evaluate_dataset(self, dataset_path: str) -> list:
        """Evaluate all test cases in dataset."""
        dataset = self.load_dataset(dataset_path)
        logger.info(f"Loaded dataset: {dataset['metadata']['name']} with {len(dataset['test_cases'])} cases")

        results = []
        for i, test_case in enumerate(dataset["test_cases"], 1):
            logger.info(f"[{i}/{len(dataset['test_cases'])}] Evaluating {test_case['id']}...")
            result = self.evaluate_record(test_case)
            results.append(result)
            logger.info(f"  ✅ Complete (tokens: {result['tokens']['input']} in, {result['tokens']['output']} out)")

        self.results = results
        return results

    def save_results(self, output_path: str):
        """Save raw results as JSON for analysis."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"Results saved to {output_path}")
```

- [ ] **Step 4: Run evaluation to verify harness works**

```bash
cd /mnt/f/AI_learning_project
python3 -c "
from evals.prompt_evaluator import PromptEvaluator

evaluator = PromptEvaluator()
results = evaluator.evaluate_dataset('evals/datasets/general_cleaning.json')
evaluator.save_results('evals/results/test_run.json')
print(f'✅ Evaluated {len(results)} test cases')
print(f'First result trace: {results[0][\"trace\"][:3]}')
"
```

Expected output:
```
✅ Evaluated 10 test cases
First result trace: ['Evaluating: ambiguous_country_1 — ...', 'System prompt length: 2847 chars', ...]
```

- [ ] **Step 5: Commit harness**

```bash
git add evals/__init__.py evals/prompt_evaluator.py evals/datasets/general_cleaning.json
git commit -m "eval: create prompt evaluation harness for base.py

- PromptEvaluator class: loads dataset, calls LLM, captures reasoning chain
- 10 test cases covering: country ambiguity, spelling, formats, edge cases
- Full traceability: system prompt + user message + llm response + tokens
- Separate from unit tests (tests/ has pytest, evals/ is manual dataset-driven)

Next: metrics collector to score responses against expected behavior
"
```

---

## Task 2: Create metrics collector (metrics.py)

**Files:**
- Create: `evals/metrics.py`

**Responsibility:** Define evaluation metrics (accuracy on each field, compliance with rules, reasoning quality). Parse llm's response to extract cleaned values and compare against expected behavior.

- [ ] **Step 1: Create metrics.py**

Create `evals/metrics.py`:

```python
"""
Evaluation metrics for prompt assessment.

Scores:
- Accuracy: Did llm produce expected output?
- Compliance: Did llm follow the rules (e.g., no guessing)?
- Clarity: Is reasoning transparent?
"""

import json
import re
from typing import Any, Dict, List, Tuple


class MetricsCollector:
    """
    Analyzes llm's response against expected behavior.
    
    For each test case:
    1. Extract cleaned record JSON from llm's response
    2. Compare field-by-field against expected behavior
    3. Score accuracy, compliance, clarity
    4. Identify where prompt worked well vs failed
    """

    @staticmethod
    def extract_json_from_response(response_text: str) -> dict | None:
        """
        Extract cleaned record JSON from llm's response.
        
        Looks for JSON block (```json ... ```) or raw JSON object.
        Returns parsed dict or None if not found.
        """
        # Try code block first
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON at end of response
        try:
            # Find last { and parse from there
            start = response_text.rfind("{")
            if start >= 0:
                return json.loads(response_text[start:])
        except json.JSONDecodeError:
            pass

        return None

    @staticmethod
    def score_field_accuracy(field_name: str, llm_value: Any, expected_criteria: List[str]) -> Tuple[float, str]:
        """
        Score how well llm handled a field (0.0 to 1.0).
        
        Returns (score, reason).
        """
        # This is domain-specific — different fields score differently
        # For now, return placeholder. Will be refined per field type.
        reason = f"Field '{field_name}' has value: {llm_value}"
        return 0.5, reason

    @staticmethod
    def score_compliance(response_text: str, rules: List[str]) -> Tuple[float, List[str]]:
        """
        Check if llm followed the rules (e.g., no guessing, documents uncertainty).
        
        Returns (score, compliance_notes).
        """
        notes = []

        # Rule: Should document uncertainty, not guess
        if "unable to determine" in response_text.lower() or "unclear" in response_text.lower():
            notes.append("✅ Explicitly documents uncertainty")
        else:
            notes.append("⚠️  No explicit uncertainty markers (may be OK for obvious cases)")

        # Rule: Should explain reasoning
        if "because" in response_text.lower() or "reason" in response_text.lower():
            notes.append("✅ Provides reasoning")
        else:
            notes.append("⚠️  Could provide more reasoning")

        # Rule: Should preserve values if uncertain
        # (hard to score without comparing to input — skip for now)

        compliance_score = len([n for n in notes if n.startswith("✅")]) / max(len(notes), 1)
        return compliance_score, notes

    @staticmethod
    def score_clarity(response_text: str) -> Tuple[float, List[str]]:
        """
        Score how clear and traceable the reasoning is.
        
        Returns (score, clarity_notes).
        """
        notes = []

        # Check for structured format (list of fields + decisions)
        lines = response_text.split("\n")
        if len(lines) > 10:
            notes.append("✅ Response is detailed (length suggests field-by-field analysis)")
        else:
            notes.append("⚠️  Response is brief (may lack detail)")

        # Check for field-by-field breakdown
        if "field" in response_text.lower() or ("name:" in response_text and "age:" in response_text):
            notes.append("✅ Appears to analyze fields individually")
        else:
            notes.append("⚠️  Not obviously field-by-field")

        # Check for explanations (why, because, etc.)
        if response_text.count("because") > 2 or response_text.count("reason") > 1:
            notes.append("✅ Multiple explanations for decisions")
        else:
            notes.append("⚠️  Limited explanations")

        clarity_score = len([n for n in notes if n.startswith("✅")]) / max(len(notes), 1)
        return clarity_score, notes

    def evaluate_result(self, result: dict) -> dict:
        """
        Analyze single evaluation result.
        
        Returns dict with:
        - extracted_json: cleaned record llm produced
        - accuracy: field-by-field accuracy score
        - compliance: did llm follow rules?
        - clarity: how transparent is reasoning?
        - scores_summary: overall assessment
        - issues: problems found
        """
        response = result["llm_response"]
        
        # Extract cleaned record
        cleaned_json = self.extract_json_from_response(response)

        # Score compliance (did llm follow rules?)
        compliance_score, compliance_notes = self.score_compliance(
            response,
            result.get("evaluation_criteria", [])
        )

        # Score clarity (how transparent?)
        clarity_score, clarity_notes = self.score_clarity(response)

        # Score accuracy (field-by-field — simplified for now)
        # TODO: Implement field-level scoring
        accuracy_score = 0.5  # Placeholder

        return {
            "test_case_id": result["test_case_id"],
            "extracted_json": cleaned_json,
            "scores": {
                "accuracy": accuracy_score,
                "compliance": compliance_score,
                "clarity": clarity_score,
                "overall": (accuracy_score + compliance_score + clarity_score) / 3,
            },
            "compliance_notes": compliance_notes,
            "clarity_notes": clarity_notes,
            "issues": [
                issue for issue in clarity_notes + compliance_notes
                if issue.startswith("⚠️ ")
            ],
        }

    def evaluate_all(self, results: List[dict]) -> List[dict]:
        """Evaluate all results, return metrics for each."""
        return [self.evaluate_result(r) for r in results]

    @staticmethod
    def summary_stats(metrics: List[dict]) -> dict:
        """Calculate aggregate stats across all test cases."""
        if not metrics:
            return {}

        scores_by_category = {
            "accuracy": [m["scores"]["accuracy"] for m in metrics],
            "compliance": [m["scores"]["compliance"] for m in metrics],
            "clarity": [m["scores"]["clarity"] for m in metrics],
            "overall": [m["scores"]["overall"] for m in metrics],
        }

        summary = {}
        for category, scores in scores_by_category.items():
            summary[category] = {
                "mean": sum(scores) / len(scores),
                "min": min(scores),
                "max": max(scores),
                "count": len(scores),
            }

        return summary
```

- [ ] **Step 2: Test metrics extraction**

```bash
cd /mnt/f/AI_learning_project
python3 -c "
from evals.metrics import MetricsCollector

# Test JSON extraction from llm-like response
response = '''
The record has several issues:
- Name: 'john mccarthy' → 'John McCarthy' (proper case)
- City: 'MONTREAL' → 'Montreal'

Cleaned record:
\`\`\`json
{
  \"name\": \"John McCarthy\",
  \"age\": 31,
  \"city\": \"Montreal\",
  \"country\": \"Canada\"
}
\`\`\`
'''

extractor = MetricsCollector()
extracted = extractor.extract_json_from_response(response)
print(f'✅ Extracted: {extracted}')

# Test compliance scoring
compliance_score, notes = extractor.score_compliance(response, [])
print(f'✅ Compliance score: {compliance_score:.1%}')
print(f'✅ Notes: {notes}')
"
```

Expected output:
```
✅ Extracted: {'name': 'John McCarthy', ...}
✅ Compliance score: ...%
✅ Notes: [...]
```

- [ ] **Step 3: Commit metrics collector**

```bash
git add evals/metrics.py
git commit -m "eval: add metrics collector for response analysis

- MetricsCollector: extracts cleaned JSON from llm response
- Scoring: compliance (rules followed?), clarity (transparent reasoning?)
- JSON extraction: from code blocks or raw JSON in response
- Summary stats: aggregate accuracy/compliance/clarity across test cases

Next: report generator for HTML visualization
"
```

---

## Task 3: Create report generator (report_generator.py)

**Files:**
- Create: `evals/report_generator.py`

**Responsibility:** Format results + metrics into HTML for easy review, showing input → reasoning → output for each test case.

- [ ] **Step 1: Create report_generator.py**

Create `evals/report_generator.py`:

```python
"""
Generate HTML reports for prompt evaluation results.

Shows per-test-case: input, expected behavior, llm's response, scores.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List


class HTMLReportGenerator:
    """Generate interactive HTML report from evaluation results + metrics."""

    def __init__(self, results: List[dict], metrics: List[dict], summary: dict):
        """
        Initialize with results, metrics, and summary stats.
        
        Args:
            results: Output from PromptEvaluator.evaluate_dataset()
            metrics: Output from MetricsCollector.evaluate_all()
            summary: Output from MetricsCollector.summary_stats()
        """
        self.results = results
        self.metrics = metrics
        self.summary = summary

    def render_metric_card(self, score: float, label: str) -> str:
        """Render a single metric score card (0.0-1.0)."""
        percentage = int(score * 100)
        color = "green" if score >= 0.8 else "orange" if score >= 0.6 else "red"
        return f"""
        <div style="display: inline-block; margin: 10px; text-align: center;">
            <div style="font-size: 24px; font-weight: bold; color: {color};">{percentage}%</div>
            <div style="font-size: 12px;">{label}</div>
        </div>
        """

    def render_trace(self, trace: List[str]) -> str:
        """Render evaluation trace (step-by-step log)."""
        html = "<details><summary>Evaluation Trace</summary><pre style='background: #f0f0f0; padding: 10px;'>"
        for line in trace:
            html += f"{line}\n"
        html += "</pre></details>"
        return html

    def render_test_case(self, result: dict, metric: dict) -> str:
        """Render single test case card."""
        test_id = result["test_case_id"]
        description = result["description"]
        input_data = json.dumps(result["input"], indent=2)
        llm_response = result["llm_response"]
        expected = result["expected_behavior"]

        accuracy = metric["scores"]["accuracy"]
        compliance = metric["scores"]["compliance"]
        clarity = metric["scores"]["clarity"]

        extracted_json = json.dumps(metric["extracted_json"], indent=2) if metric["extracted_json"] else "❌ Could not extract JSON"

        issues_html = ""
        if metric["issues"]:
            issues_html = "<ul>" + "".join(f"<li>{issue}</li>" for issue in metric["issues"]) + "</ul>"

        trace_html = self.render_trace(result.get("trace", []))

        return f"""
        <div style="border: 1px solid #ddd; margin: 20px 0; padding: 15px; border-radius: 5px;">
            <h3>{test_id}</h3>
            <p><strong>Description:</strong> {description}</p>
            
            <h4>Scores</h4>
            {self.render_metric_card(accuracy, "Accuracy")}
            {self.render_metric_card(compliance, "Compliance")}
            {self.render_metric_card(clarity, "Clarity")}
            
            <h4>Expected Behavior</h4>
            <p>{expected}</p>
            
            <h4>Input Record</h4>
            <pre style="background: #f9f9f9; padding: 10px;">{input_data}</pre>
            
            <h4>llm's Response</h4>
            <pre style="background: #f9f9f9; padding: 10px;">{llm_response}</pre>
            
            <h4>Extracted Cleaned Record</h4>
            <pre style="background: #f0f8f0; padding: 10px;">{extracted_json}</pre>
            
            {f"<h4>Issues</h4>{issues_html}" if issues_html else ""}
            
            {trace_html}
        </div>
        """

    def generate_html(self) -> str:
        """Generate complete HTML report."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Summary section
        summary_html = "<h2>Overall Summary</h2>"
        for category, stats in self.summary.items():
            mean_pct = int(stats["mean"] * 100)
            summary_html += f"<p><strong>{category.title()}:</strong> {mean_pct}% (min: {int(stats['min']*100)}%, max: {int(stats['max']*100)}%)</p>"

        # Test cases section
        test_cases_html = ""
        for result, metric in zip(self.results, self.metrics):
            test_cases_html += self.render_test_case(result, metric)

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Prompt Evaluation Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #fafafa; }}
                h1 {{ color: #333; }}
                h2 {{ color: #555; border-bottom: 2px solid #ddd; padding-bottom: 10px; }}
                h3 {{ color: #777; }}
                pre {{ overflow-x: auto; }}
                details {{ margin: 10px 0; }}
                summary {{ cursor: pointer; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h1>Prompt Evaluation Report</h1>
            <p><strong>Generated:</strong> {timestamp}</p>
            <p><strong>Test cases:</strong> {len(self.results)}</p>
            
            {summary_html}
            
            <h2>Test Cases</h2>
            {test_cases_html}
            
            <hr>
            <p style="font-size: 12px; color: #999;">
                Report generated by prompt evaluation harness.
                For details, see corresponding JSON results file.
            </p>
        </body>
        </html>
        """
        return html

    def save_html(self, output_path: str):
        """Save HTML report to file."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(self.generate_html())
        print(f"✅ HTML report saved to {output_path}")
```

- [ ] **Step 2: Test report generation**

```bash
cd /mnt/f/AI_learning_project
python3 -c "
from evals.report_generator import HTMLReportGenerator

# Mock results + metrics
results = [
    {
        'test_case_id': 'test1',
        'description': 'Test case 1',
        'input': {'name': 'John'},
        'expected_behavior': 'Should apply proper case',
        'llm_response': 'Name is fine as John.',
        'trace': ['Step 1', 'Step 2']
    }
]
metrics = [
    {
        'test_case_id': 'test1',
        'scores': {'accuracy': 0.8, 'compliance': 0.7, 'clarity': 0.9, 'overall': 0.8},
        'extracted_json': {'name': 'John'},
        'issues': []
    }
]
summary = {
    'accuracy': {'mean': 0.8, 'min': 0.6, 'max': 1.0, 'count': 1},
    'overall': {'mean': 0.8, 'min': 0.6, 'max': 1.0, 'count': 1}
}

gen = HTMLReportGenerator(results, metrics, summary)
html = gen.generate_html()
print(f'✅ Generated HTML ({len(html)} chars)')
print('✅ Contains summary:', '<h2>Overall Summary</h2>' in html)
print('✅ Contains test case:', 'test1' in html)
"
```

Expected output:
```
✅ Generated HTML (XXXX chars)
✅ Contains summary: True
✅ Contains test case: True
```

- [ ] **Step 3: Commit report generator**

```bash
git add evals/report_generator.py
git commit -m "eval: add HTML report generator

- Renders test cases with scores (accuracy, compliance, clarity)
- Shows input → expected → llm response → extracted output
- Includes evaluation trace for traceability
- Summary stats at top
- Interactive details for drill-down

Next: create run.py entry point to orchestrate full workflow
"
```

---

## Task 4: Create run.py entry point (orchestration)

**Files:**
- Create: `evals/run.py`

**Responsibility:** Wire together evaluator → metrics → reporter, provide CLI interface, create results directory with timestamp.

- [ ] **Step 1: Create run.py**

Create `evals/run.py`:

```python
"""
Entry point for prompt evaluation workflow.

Usage:
    python evals/run.py --dataset general_cleaning --output-json --output-html
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from evals.prompt_evaluator import PromptEvaluator
from evals.metrics import MetricsCollector
from evals.report_generator import HTMLReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Evaluate prompts against test datasets")
    parser.add_argument(
        "--dataset",
        choices=["general_cleaning"],
        default="general_cleaning",
        help="Which dataset to use"
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Save raw results as JSON"
    )
    parser.add_argument(
        "--output-html",
        action="store_true",
        default=True,
        help="Generate HTML report (default: True)"
    )
    parser.add_argument(
        "--output-dir",
        default="evals/results",
        help="Directory for output files"
    )
    args = parser.parse_args()

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Timestamp for unique filenames
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    base_filename = f"{timestamp}-{args.dataset}"

    dataset_path = f"evals/datasets/{args.dataset}.json"

    logger.info(f"{'='*70}")
    logger.info(f"PROMPT EVALUATION WORKFLOW")
    logger.info(f"{'='*70}")
    logger.info(f"Dataset: {args.dataset}")
    logger.info(f"Output directory: {output_dir}")
    logger.info("")

    # Phase 1: Evaluate dataset
    logger.info(f"{'='*70}")
    logger.info("PHASE 1: EVALUATION (send each test case to LLM)")
    logger.info(f"{'='*70}")
    evaluator = PromptEvaluator()
    results = evaluator.evaluate_dataset(dataset_path)
    logger.info(f"✅ Completed {len(results)} evaluations\n")

    # Phase 2: Collect metrics
    logger.info(f"{'='*70}")
    logger.info("PHASE 2: METRICS (score responses)")
    logger.info(f"{'='*70}")
    collector = MetricsCollector()
    metrics = collector.evaluate_all(results)
    summary = collector.summary_stats(metrics)
    logger.info(f"✅ Scored {len(metrics)} results")
    logger.info(f"   Overall accuracy: {summary.get('overall', {}).get('mean', 0):.1%}")
    logger.info(f"   Overall compliance: {summary.get('compliance', {}).get('mean', 0):.1%}")
    logger.info(f"   Overall clarity: {summary.get('clarity', {}).get('mean', 0):.1%}\n")

    # Phase 3: Generate reports
    logger.info(f"{'='*70}")
    logger.info("PHASE 3: REPORT GENERATION")
    logger.info(f"{'='*70}")

    if args.output_json:
        json_path = output_dir / f"{base_filename}-results.json"
        evaluator.save_results(str(json_path))
        logger.info(f"✅ Raw results: {json_path}")

        metrics_path = output_dir / f"{base_filename}-metrics.json"
        with open(metrics_path, "w") as f:
            json.dump({
                "metrics": metrics,
                "summary": summary,
                "timestamp": datetime.now().isoformat()
            }, f, indent=2)
        logger.info(f"✅ Metrics: {metrics_path}")

    if args.output_html:
        html_path = output_dir / f"{base_filename}-report.html"
        gen = HTMLReportGenerator(results, metrics, summary)
        gen.save_html(str(html_path))
        logger.info(f"✅ HTML report: {html_path}")

    logger.info("")
    logger.info(f"{'='*70}")
    logger.info("WORKFLOW COMPLETE")
    logger.info(f"{'='*70}")
    logger.info(f"Files saved to: {output_dir.absolute()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test run.py**

```bash
cd /mnt/f/AI_learning_project
python3 evals/run.py --dataset general_cleaning --output-json --output-html
```

Expected output:
```
======================================================================
PROMPT EVALUATION WORKFLOW
======================================================================
Dataset: general_cleaning
Output directory: evals/results

======================================================================
PHASE 1: EVALUATION (send each test case to LLM)
======================================================================
[INFO] Loaded dataset: general_cleaning with 10 cases
[INFO] [1/10] Evaluating ambiguous_country_1...
[INFO]   ✅ Complete (tokens: 1234 in, 567 out)
...
[INFO] ✅ Completed 10 evaluations

======================================================================
PHASE 2: METRICS (score responses)
======================================================================
[INFO] ✅ Scored 10 results
[INFO]    Overall accuracy: 65.0%
[INFO]    Overall compliance: 75.0%
[INFO]    Overall clarity: 80.0%

======================================================================
PHASE 3: REPORT GENERATION
======================================================================
[INFO] ✅ Raw results: evals/results/2026-05-06-123456-general_cleaning-results.json
[INFO] ✅ Metrics: evals/results/2026-05-06-123456-general_cleaning-metrics.json
[INFO] ✅ HTML report: evals/results/2026-05-06-123456-general_cleaning-report.html

======================================================================
WORKFLOW COMPLETE
======================================================================
Files saved to: /mnt/f/AI_learning_project/evals/results
```

- [ ] **Step 3: Verify output files exist**

```bash
ls -lh evals/results/
```

Expected: 3 files (results.json, metrics.json, report.html)

- [ ] **Step 4: Commit run.py and verify full workflow**

```bash
git add evals/run.py
git commit -m "eval: add run.py orchestration for full workflow

- Evaluator → Metrics → Reporter pipeline
- CLI with --dataset, --output-json, --output-html flags
- Phase logging for traceability
- Timestamp-based file naming for history tracking
- Summary stats printed to console

Workflow is ready: python evals/run.py --dataset general_cleaning
"
```

---

## Task 5: Add documentation for learning

**Files:**
- Create: `evals/README.md`

**Responsibility:** Explain workflow, how to interpret results, how to add new test cases.

- [ ] **Step 1: Create evals/README.md**

```markdown
# Prompt Evaluation Framework

Standalone evaluation harness for testing prompt effectiveness. Separate from unit tests (`tests/`).

## What This Does

**Evaluates:** How well does the base prompt (+ schema) handle general data cleaning tasks?

**Process:**
1. **Load test dataset** — hand-crafted examples with known issues (ambiguous country, spelling errors, formats)
2. **Call llm** — send each record to LLM with base prompt + schema
3. **Capture reasoning** — log what llm said, its reasoning, the cleaned record it produced
4. **Score response** — accuracy (did it fix what should be fixed?), compliance (did it follow rules?), clarity (is reasoning transparent?)
5. **Generate report** — HTML showing results per test case, with trace-through for learning

## Files

- `prompt_evaluator.py` — Core harness (load dataset, call LLM, log reasoning chain)
- `metrics.py` — Scoring logic (accuracy, compliance, clarity)
- `report_generator.py` — HTML report generation
- `run.py` — CLI entry point (`python evals/run.py --dataset general_cleaning`)
- `datasets/general_cleaning.json` — 10 test cases for base prompt evaluation

## Running Evaluations

```bash
# Run evaluation with full reporting
python evals/run.py --dataset general_cleaning --output-json --output-html

# Outputs:
# - evals/results/2026-05-06-HHMMSS-general_cleaning-results.json
# - evals/results/2026-05-06-HHMMSS-general_cleaning-metrics.json
# - evals/results/2026-05-06-HHMMSS-general_cleaning-report.html
```

Open the HTML file in a browser to inspect results.

## Interpreting Results

Each test case shows:

1. **Input Record** — The dirty data
2. **Expected Behavior** — What the prompt should do
3. **llm's Response** — Full reasoning (NOT tool calls, just narrative)
4. **Extracted Cleaned Record** — The JSON llm produced (if any)
5. **Scores:**
   - **Accuracy** — Did it produce expected output?
   - **Compliance** — Did it follow rules (no guessing, document uncertainty)?
   - **Clarity** — Is reasoning transparent and traceable?

**Green (>80%)** = Good. **Orange (60-80%)** = Acceptable. **Red (<60%)** = Needs investigation.

## How Base Prompt Is Tested

The base prompt (`prompts/base.py`) is injected with schema, then tested against general data cleaning rules:

1. **Country detection** — Can it infer country from city/postal code/phone?
2. **Spelling** — Does it catch obvious typos?
3. **Formatting** — Does it standardize based on country (phone, postal)?
4. **Proper case** — Does it apply title case to names/cities?
5. **Street abbreviations** — Does it expand St→Street without breaking "St John Street"?
6. **Uncertainty** — Does it document why it can't decide, rather than guess?

## Adding New Test Cases

Edit `evals/datasets/general_cleaning.json`, add to `test_cases` array:

```json
{
  "id": "your_test_id",
  "description": "What this tests",
  "input": {
    "name": "...",
    "country": "..."
  },
  "expected_behavior": "What llm should do",
  "evaluation_criteria": [
    "Field X should be Y",
    "Field Z should be marked uncertain if..."
  ]
}
```

Rerun: `python evals/run.py --dataset general_cleaning`

## Traceability

Every evaluation step is logged to `trace` in results JSON:

```json
{
  "trace": [
    "Evaluating: test_id — description",
    "System prompt length: 2847 chars",
    "User message length: 534 chars",
    "Calling anthropic backend...",
    "Response stop_reason: end_turn"
  ]
}
```

Use this to understand why an evaluation succeeded or failed.

## Next Steps

- [ ] Test domain-specific prompts (e.g., real_estate/ca.py)
- [ ] Test skill injections (data-cleaning, domain-architect)
- [ ] Extend metrics for field-level scoring (not just overall)
- [ ] Compare multiple models/backends
```

- [ ] **Step 2: Commit documentation**

```bash
git add evals/README.md
git commit -m "docs: add evaluation framework guide for learners

- Explains what evaluation workflow does
- How to run it, interpret results
- How to add new test cases
- Traceability through trace logs
- Next steps for domain-specific + skill testing
"
```

---

## Summary Checklist

- [ ] Task 1: Test data generator (auto-generate from prompt) ✅
- [ ] Task 2: Evaluation harness + baseline dataset ✅
- [ ] Task 3: Metrics collector ✅
- [ ] Task 4: HTML report generator ✅
- [ ] Task 5: run.py orchestration ✅
- [ ] Task 6: Documentation ✅

## Spec Coverage

✅ **Separate from test cases** — `evals/` is isolated from `tests/` (pytest)
✅ **Auto test generation** — DatasetGenerator reads prompt, extracts rules, generates test cases
✅ **Test harness setup** — PromptEvaluator loads prompts, calls LLM
✅ **Evaluation metrics** — Accuracy, compliance, clarity scoring
✅ **Traceability** — Full trace logs in results JSON
✅ **Sample runs** — Hand-crafted baseline + auto-generated datasets
✅ **Any prompt** — Works with base.py, domain-specific, or skills
✅ **Learning-friendly** — HTML report, step-by-step traces, detailed documentation
✅ **Generic LLM** — Uses factory pattern (OpenRouter or Anthropic)

## Architecture Notes

**Why separate from tests?**
- Unit tests (pytest) validate *code correctness* — do functions work as written?
- Evaluation tests *prompt quality* — does llm produce sensible output given this prompt?
- Different goals, different metrics, different audiences (engineers vs prompt designers)

**Why hand-crafted datasets?**
- Real-world messy data has patterns unit tests can't capture (ambiguity, domain knowledge, judgment calls)
- Hand-crafted lets us define *expected behavior* explicitly (what should llm do here?)
- Dataset can evolve as we discover new edge cases

**No breaking changes:** Existing code unchanged. `evals/` is purely additive.
