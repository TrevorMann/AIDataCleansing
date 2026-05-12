"""Skill registry for fast O(1) lookup and management."""

import yaml
from typing import Any, Dict, Optional
from pathlib import Path


class SkillRegistry:
    """Fast in-memory skill registry loaded at startup."""

    def __init__(self):
        """Initialize empty registry."""
        self.skills: Dict[str, Any] = {}  # {skill_name} → SkillClass instance
        self.metadata: Dict[str, Dict] = {}  # {skill_name} → metadata dict
        self.config: Dict[str, Any] = {}  # merged config
        self.runtime: Dict[str, Any] = {}  # runtime resources (e.g. pg_conn)

    @classmethod
    def load(cls, domain: str, config_path: Optional[str] = None, runtime: Optional[Dict] = None) -> "SkillRegistry":
        """Load all skills for domain at startup (single load, reuse across batch).

        Args:
            domain: Domain name (e.g., 'real_estate')
            config_path: Optional custom config path
            runtime: Optional runtime resources dict (e.g. {"pg_conn": conn})

        Returns:
            Populated registry instance
        """
        registry = cls()
        registry.runtime = runtime or {}
        registry.load_domain(domain, config_path)
        return registry

    def load_domain(self, domain: str, config_path: Optional[str] = None):
        """Load skills from YAML for domain.

        Args:
            domain: Domain name
            config_path: Optional custom path (defaults to skills/{domain}/skills.yaml)
        """
        if config_path is None:
            config_path = Path(__file__).parent / domain / "skills.yaml"
        else:
            config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Skills file not found: {config_path}")

        with open(config_path) as f:
            skills_config = yaml.safe_load(f)

        self.config = skills_config.get("config", {})

        # Register each skill from YAML
        for skill_name, skill_def in skills_config.get("skills", {}).items():
            self._register_skill(skill_name, skill_def, domain)

    def _register_skill(self, skill_name: str, skill_def: Dict, domain: str):
        """Register a single skill from definition.

        Args:
            skill_name: Skill name
            skill_def: Skill definition dict (class, tools, config, etc.)
            domain: Domain name
        """
        # Import skill class dynamically
        class_path = skill_def.get("class")
        if not class_path:
            raise ValueError(f"Skill {skill_name} missing 'class' definition")

        module_name, class_name = class_path.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        skill_class = getattr(module, class_name)

        # Merge config: defaults + domain + skill-specific
        merged_config = {**self.config}
        merged_config.update(skill_def.get("config", {}))

        # Resolve runtime placeholders like "${runtime.pg_conn}"
        for k, v in list(merged_config.items()):
            if isinstance(v, str) and v.startswith("${runtime.") and v.endswith("}"):
                key = v[len("${runtime."):-1]
                merged_config[k] = self.runtime.get(key)

        # Instantiate skill with merged config
        skill_instance = skill_class(merged_config)
        skill_instance.domain = domain

        # Load skill documentation (markdown)
        skill_doc = None
        skill_doc_path = skill_def.get("skill_doc")
        if skill_doc_path:
            try:
                doc_full_path = Path(__file__).parent.parent / skill_doc_path
                with open(doc_full_path) as f:
                    skill_doc = f.read()
            except Exception:
                pass  # Skill doc optional

        # Store instance and metadata
        self.skills[skill_name] = skill_instance
        self.metadata[skill_name] = {
            "class": class_path,
            "skill_doc": skill_doc,  # Full markdown content
            "tools": skill_def.get("tools", []),
            "cost": skill_def.get("cost", "medium"),
            "phase": skill_def.get("phase"),
            "latency_estimate_ms": skill_def.get("latency_estimate_ms", 500),
            "depends_on": skill_def.get("depends_on", []),
        }

    def get(self, skill_name: str) -> Optional[Any]:
        """O(1) lookup - get skill by name.

        Args:
            skill_name: Name of skill

        Returns:
            Skill instance or None if not found
        """
        return self.skills.get(skill_name)

    def get_all(self) -> Dict[str, Any]:
        """Get all registered skills."""
        return self.skills.copy()

    def get_metadata(self, skill_name: str) -> Optional[Dict]:
        """Get skill metadata (tools, cost, latency, etc.)."""
        return self.metadata.get(skill_name)

    def list_skills(self) -> list:
        """List all registered skill names."""
        return list(self.skills.keys())

    def get_skill_documentation(self, skill_name: str) -> Optional[str]:
        """Get skill documentation (markdown) for LLM reasoning.

        Args:
            skill_name: Name of skill

        Returns:
            Skill documentation markdown or None
        """
        meta = self.metadata.get(skill_name)
        if meta:
            return meta.get("skill_doc")
        return None

    def validate_dependencies(self):
        """Detect missing deps and circular dependencies. Raises ValueError."""
        for name in self.skills:
            deps = self.metadata[name].get("depends_on", [])
            for dep in deps:
                if dep not in self.skills:
                    raise ValueError(f"Skill '{name}' depends on unknown skill: '{dep}'")
        self._detect_cycles()

    def _detect_cycles(self):
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.skills}

        def dfs(node):
            color[node] = GRAY
            for dep in self.metadata[node].get("depends_on", []):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    raise ValueError(f"Circular dependency detected: {node} → {dep}")
                if color[dep] == WHITE:
                    dfs(dep)
            color[node] = BLACK

        for node in list(self.skills.keys()):
            if color[node] == WHITE:
                dfs(node)

    def topological_sort(self, skill_names: list) -> list:
        """Return skill_names in dependency order. Drops unknown skill names."""
        valid = [s for s in skill_names if s in self.skills]
        visited = set()
        order = []

        def visit(n):
            if n in visited:
                return
            visited.add(n)
            for dep in self.metadata[n].get("depends_on", []):
                if dep in set(valid):
                    visit(dep)
            order.append(n)

        for s in valid:
            visit(s)
        return order

    def skills_by_cost(self, cost: str) -> list:
        """All skills with given cost level, in dependency order."""
        matches = [n for n, m in self.metadata.items() if m.get("cost") == cost]
        return self.topological_sort(matches)

    def __repr__(self):
        return f"SkillRegistry({len(self.skills)} skills: {', '.join(self.list_skills())})"
