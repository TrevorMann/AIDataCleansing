import json
import logging
from schema_discovery import format_schema_for_prompt
from llm_client_factory import build_message_kwargs, log_usage, build_system_param

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract database filter criteria from natural language requests. "
    "Return ONLY valid JSON — a single object with field/value pairs. "
    "Return {} if the user wants all records or specifies no filter. "
    "Return null if the user specifies a filter that cannot be mapped to any schema field. "
    "Use only field names that appear in the provided schema."
)


class ScopeInterpreter:
    def __init__(self, client, backend: str, model: str):
        self._client = client
        self._backend = backend
        self._model = model

    def interpret(self, user_query: str, domain: str, db_path: str) -> dict | None:
        """
        Returns:
          {"field": "value"}  — filter found, apply to fetch
          {}                  — user wants all records
          None                — user specified something unresolvable in schema
        """
        schema = format_schema_for_prompt(db_path, domain)
        user_msg = f"Schema:\n{schema}\n\nUser request: {user_query}"

        kwargs = build_message_kwargs(self._backend)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            system=build_system_param(self._backend, _SYSTEM),
            messages=[{"role": "user", "content": user_msg}],
            **kwargs,
        )
        log_usage(self._backend, response.usage)

        raw = next(
            (b.text for b in response.content if hasattr(b, "text")),
            None,
        )
        if not raw:
            return None

        try:
            result = json.loads(raw.strip())
            if result is None:
                return None
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            logger.warning("ScopeInterpreter: invalid JSON from LLM: %r", raw)
            return None
