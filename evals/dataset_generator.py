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
import re
from datetime import datetime
from pathlib import Path

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
        schema_context = self.schema if self.schema else (
            "Generic record with: name, age, city, address, postal_code, "
            "municipality, state_province, country, phone"
        )

        generation_prompt = f"""Generate {num_cases} diverse test cases for a data cleaning prompt.

RULES TO TEST:
{rules}

SCHEMA:
{schema_context}

Generate test cases that cover:
1. Happy path (data already clean)
2. Single field issues (spelling, case, format)
3. Multi-field issues (ambiguity, missing data)
4. Edge cases (unusual but valid)
5. Uncertainty cases (when rules say "don't guess")

Output a JSON array of test case objects. Each object must have EXACTLY these keys:
- "id": unique string like "test_case_1"
- "description": one sentence describing what this tests
- "input": object with field name/value pairs (use null for missing fields)
- "expected_behavior": one sentence describing what the cleaner should do
- "evaluation_criteria": array of strings, each a testable criterion

Output ONLY the JSON array, no preamble, no code fences."""

        system_param = build_system_param(self.backend, "You are a data QA expert. Output valid JSON only.")
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
            "[]"
        )

        # Try to parse as JSON array first
        try:
            test_cases = json.loads(response_text.strip())
            if isinstance(test_cases, list):
                return test_cases[:num_cases]
        except json.JSONDecodeError:
            pass

        # Fallback: extract JSON array with regex
        match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if match:
            try:
                test_cases = json.loads(match.group(0))
                if isinstance(test_cases, list):
                    return test_cases[:num_cases]
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse generated test cases as JSON array")
        return []

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
        logger.info(f"Extracted {len(rules.splitlines())} rule lines")

        # Step 2: Generate test cases
        logger.info("Step 2: Generating test cases...")
        test_cases = self.generate_test_cases(rules, num_cases=num_cases)
        logger.info(f"Generated {len(test_cases)} test cases")

        # Step 3: Package as dataset
        dataset = {
            "metadata": {
                "name": self.dataset_name,
                "description": f"Auto-generated dataset from prompt ({num_cases} requested, {len(test_cases)} generated)",
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
