"""Base skill class for all domain skills."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseSkill(ABC):
    """Base class for all skills. Subclass for domain-specific implementations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize skill with configuration.

        Args:
            config: Skill-specific configuration dict
        """
        self.config = config or {}
        self.name = self.__class__.__name__
        self.domain = None  # Subclasses set this
        self._audit_entries: List[Dict] = []

    @abstractmethod
    def run(self, input_data: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute skill logic.

        Args:
            input_data: Input dict (record being processed)
            tools: Available tools for this skill

        Returns:
            Modified input dict with skill results
        """
        pass

    def validate_config(self, required_keys: list) -> bool:
        """Validate that required config keys are present."""
        return all(key in self.config for key in required_keys)

    def log_decision(self, decision: str, reason: str, confidence: float = 1.0) -> dict:
        """Log skill decision for audit trail.

        Args:
            decision: The decision made by the skill
            reason: The reasoning behind the decision
            confidence: Confidence level (0.0-1.0)

        Returns:
            Decision dict (for backward compat; also appends to _audit_entries)
        """
        entry = {
            "skill": self.name,
            "decision": decision,
            "reason": reason,
            "confidence": confidence,
        }
        self._audit_entries.append(entry)
        return entry

    def get_audit(self) -> List[Dict]:
        """Retrieve all audit entries accumulated so far.

        Returns:
            List of audit entries (dicts)
        """
        return list(self._audit_entries)

    def clear_audit(self):
        """Clear all accumulated audit entries."""
        self._audit_entries = []
