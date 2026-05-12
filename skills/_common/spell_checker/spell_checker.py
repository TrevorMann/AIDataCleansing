"""Domain-agnostic spell checker skill — symspellpy base + domain override table."""

import os
from typing import Any, Dict, List, Optional

from skills.base import BaseSkill


class SpellChecker(BaseSkill):
    """Correct obvious spelling mistakes in configured text fields.

    Uses symspellpy (general English dictionary) as the primary engine.
    Domain-specific proper nouns are handled via an override table loaded from DB.
    Only fields listed in text_fields config are ever touched.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.domain = self.config.get("domain", "")
        self.threshold = self.config.get("threshold", 0.85)
        self.text_fields: List[str] = self.config.get("text_fields", [])
        self.max_edit_distance = self.config.get("max_edit_distance", 2)

        self._sym_spell = self._load_symspell()
        conn = self.config.get("pg_conn")
        self._overrides: Dict[str, str] = self._load_overrides(conn)

    def _load_symspell(self):
        from symspellpy import SymSpell
        ss = SymSpell(max_dictionary_edit_distance=self.max_edit_distance, prefix_length=7)
        import symspellpy as _symspellpy_pkg
        dict_path = os.path.join(
            os.path.dirname(_symspellpy_pkg.__file__),
            "frequency_dictionary_en_82_765.txt",
        )
        ss.load_dictionary(dict_path, term_index=0, count_index=1)
        return ss

    def _load_overrides(self, conn) -> Dict[str, str]:
        if conn is None:
            return {}
        try:
            from cleaning.spell_corrections_data import get_corrections_dict
            return get_corrections_dict(conn, self.domain)
        except Exception:
            return {}

    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        self.clear_audit()
        for field in self.text_fields:
            value = input_data.get(field)
            if not value or not isinstance(value, str):
                continue
            corrected = self._correct(value, field)
            if corrected != value:
                input_data[field] = corrected
        return input_data

    def _correct(self, text: str, field: str) -> str:
        from symspellpy import Verbosity

        text_lower = text.lower()

        # 1. Domain override table — exact match, confidence=1.0
        if text_lower in self._overrides:
            corrected = self._overrides[text_lower]
            out = corrected.title() if text[0].isupper() else corrected
            self.log_decision(
                f"{field}: '{text}' → '{out}'",
                "domain override",
                confidence=1.0,
            )
            return out

        # 2. symspellpy — general English dictionary
        suggestions = self._sym_spell.lookup(
            text_lower, Verbosity.CLOSEST, max_edit_distance=self.max_edit_distance
        )
        if not suggestions:
            return text
        best = suggestions[0]
        if best.term == text_lower:
            return text  # already correct
        confidence = max(0.0, 1.0 - best.distance * 0.1)
        if confidence < self.threshold:
            return text
        out = best.term.title() if text[0].isupper() else best.term
        self.log_decision(
            f"{field}: '{text}' → '{out}'",
            f"symspellpy (edit_dist={best.distance})",
            confidence=confidence,
        )
        return out
