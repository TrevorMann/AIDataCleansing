"""
Prompt evaluation harness for base.py + schema.

Loads test dataset, sends each record to LLM with base prompt + schema,
captures full reasoning chain and output.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from llm_client_factory import create_client, build_system_param, build_message_kwargs, log_usage
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
        user_message = (
            f"Clean this data record according to the rules above.\n\n"
            f"Record:\n{json.dumps(input_record, indent=2)}\n\n"
            f"Please:\n"
            f"1. List each field and whether it needs cleaning\n"
            f"2. For fields that need cleaning, explain your reasoning\n"
            f"3. Provide the cleaned record as JSON at the end\n"
            f"4. For any field you cannot confidently clean, explain why"
        )

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

        log_usage(self.backend, response.usage)
        trace.append(f"Response stop_reason: {response.stop_reason}")

        # Step 4: Extract response content
        llm_response = next(
            (b.text for b in response.content if hasattr(b, "text")),
            "No text response"
        )
        trace.append(f"LLM response length: {len(llm_response)} chars")

        return {
            "test_case_id": test_case["id"],
            "description": test_case["description"],
            "input": input_record,
            "expected_behavior": test_case.get("expected_behavior", ""),
            "evaluation_criteria": test_case.get("evaluation_criteria", []),
            "system_prompt": system_prompt,
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
        logger.info(
            f"Loaded dataset: {dataset['metadata']['name']} "
            f"with {len(dataset['test_cases'])} cases"
        )

        results = []
        for i, test_case in enumerate(dataset["test_cases"], 1):
            logger.info(f"[{i}/{len(dataset['test_cases'])}] Evaluating {test_case['id']}...")
            result = self.evaluate_record(test_case)
            results.append(result)
            logger.info(
                f"  ✅ Complete "
                f"(tokens: {result['tokens']['input']} in, {result['tokens']['output']} out)"
            )

        self.results = results
        return results

    def save_results(self, output_path: str):
        """Save raw results as JSON for analysis."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"Results saved to {output_path}")
