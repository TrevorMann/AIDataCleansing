"""Base agent class for specialized agent team members."""

from typing import Any, Dict, List
from skills.base import BaseSkill


class BaseAgent:
    """Base agent for agent teams. Specializes in specific skills."""

    def __init__(self, name: str, skills: List[str], registry: "SkillRegistry", tools: Dict[str, Any] = None):
        """Initialize agent with assigned skills.

        Args:
            name: Agent name (e.g., 'AddressCleaningAgent')
            skills: List of skill names this agent specializes in
            registry: SkillRegistry instance (for O(1) lookup)
            tools: Available tools dict
        """
        self.name = name
        self.skill_names = skills
        self.registry = registry
        self.tools = tools or {}

    def execute(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Execute all assigned skills on record in sequence.

        Args:
            record: Record to process

        Returns:
            Processed record
        """
        record_decisions = []
        for skill_name in self.skill_names:
            skill = self.registry.get(skill_name)
            if not skill:
                print(f"Warning: Skill {skill_name} not found in registry")
                continue
            record = skill.run(record, self.tools)
            if "_decisions" in record:
                record_decisions.extend(record["_decisions"])
                del record["_decisions"]  # consume so next skill starts clean
        record["_decisions"] = record_decisions
        return record

    def __repr__(self):
        return f"{self.name}({', '.join(self.skill_names)})"
