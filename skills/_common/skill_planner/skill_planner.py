"""LLM-driven skill planner — outputs ordered execution plan for a record."""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from db.schema_config import get_framework_schema
from skills.base import BaseSkill


class SkillPlanner(BaseSkill):
    """Plan which skills to run for a record, using LLM + plan cache."""

    PLANNER_SYSTEM = (
        "You orchestrate a data-cleaning pipeline for the {domain} domain.\n"
        "Given a record and a skill menu, output JSON only (no prose):\n"
        '  {{"plan": ["skill1", "skill2"], "reasoning": "..."}}\n\n'
        "Rules:\n"
        "- Only output skill names that appear in the menu (no inventions)\n"
        "- Cheap deterministic skills first (cost: low)\n"
        "- web_search_enricher only when confidence < 0.70 AND identifiable gap\n"
        "- Respect depends_on constraints\n"
        "- Omit skills that clearly cannot help for this record's gaps\n"
    )

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.conn = self.config.get("pg_conn")
        self.cache_ttl_hours = self.config.get("plan_cache_ttl_hours", 24)
        self._llm = self.config.get("llm_client")  # injected or built lazily
        self.framework_schema = self.config.get("framework_schema", get_framework_schema())

    def _get_llm(self):
        if self._llm is None:
            from cleaning.llm_client import build_client_for_tier
            self._llm = build_client_for_tier(self.config.get("tier", "fast"))
        return self._llm

    def run(self, record: Dict[str, Any], tools: Dict[str, Any] = None) -> Dict[str, Any]:
        registry = (tools or {}).get("registry")
        if not registry:
            return record

        sig = self._record_signature(record, registry)
        cached = self._cache_get(sig)
        if cached:
            record["_planned_skills"] = cached["plan"]
            record["_plan_reasoning"] = cached["reasoning"]
            record["_plan_source"] = "cache"
            return record

        menu = self._build_menu(registry)
        prompt = self._build_prompt(record, menu)

        try:
            llm = self._get_llm()
            resp = llm.messages_create(
                system=self.PLANNER_SYSTEM.format(domain=self.domain or "general"),
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=512,
            )
            text = next((b.text for b in resp.content if hasattr(b, "text")), "{}")
        except Exception as e:
            record["_planned_skills"] = []
            record["_plan_reasoning"] = f"LLM error: {str(e)[:80]}"
            record["_plan_source"] = "error"
            return record

        plan = self._parse_and_validate(text, registry)
        record["_planned_skills"] = plan["plan"]
        record["_plan_reasoning"] = plan["reasoning"]
        record["_plan_source"] = "llm"

        self._cache_set(sig, plan)
        return record

    def _record_signature(self, record: dict, registry) -> str:
        shape = {
            "fields_present": sorted(k for k, v in record.items() if v and not k.startswith("_")),
            "conf_bucket": round(record.get("_triage_data_confidence", 1.0), 1),
            "route": record.get("_triage_route"),
            "gaps": sorted(record.get("_gap_hints", [])),
            "domain": self.domain,
            "skills_version": str(registry.config.get("version", "1.0")),
        }
        return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()

    def _build_menu(self, registry) -> List[dict]:
        menu = []
        for name in registry.list_skills():
            meta = registry.get_metadata(name) or {}
            menu.append({
                "name": name,
                "doc": (meta.get("skill_doc") or "")[:800],
                "cost": meta.get("cost", "medium"),
                "latency_ms": meta.get("latency_estimate_ms", 500),
                "depends_on": meta.get("depends_on", []),
            })
        return menu

    def _get_annotation_context(self) -> str:
        """Query column_metadata for domain annotations. Returns '' if unavailable."""
        if not self.conn or not self.domain:
            return ""
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"SELECT table_name, column_name, description "
                    f"FROM {self.framework_schema}.column_metadata WHERE domain = %s "
                    f"ORDER BY table_name, column_name",
                    (self.domain,),
                )
                rows = cur.fetchall()
            if not rows:
                return ""
            lines = [f"## Column Annotations (domain: {self.domain})"]
            for table, col, desc in rows:
                lines.append(f"{table}.{col}: {desc}")
            return "\n".join(lines) + "\n\n"
        except Exception:
            return ""

    def _build_prompt(self, record: dict, menu: List[dict]) -> str:
        safe_record = {k: v for k, v in record.items() if not k.startswith("_") or k in (
            "_triage_route", "_triage_data_confidence", "_gap_hints", "_unknown_fsa",
            "_municipality_confidence",
        )}
        menu_text = json.dumps(
            [{"name": m["name"], "cost": m["cost"], "depends_on": m["depends_on"]} for m in menu],
            indent=2,
        )
        annotation_context = self._get_annotation_context()
        return (
            f"{annotation_context}"
            f"Record:\n{json.dumps(safe_record, indent=2)}\n\n"
            f"Available skills:\n{menu_text}\n\n"
            "Output JSON plan."
        )

    def _parse_and_validate(self, text: str, registry) -> dict:
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            try:
                data = json.loads(m.group(0)) if m else {}
            except Exception:
                data = {}

        valid_skills = set(registry.list_skills())
        plan = [s for s in data.get("plan", []) if s in valid_skills]
        plan = registry.topological_sort(plan)
        return {"plan": plan, "reasoning": data.get("reasoning", "")}

    def _cache_get(self, sig: str) -> Optional[dict]:
        if not self.conn:
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"SELECT plan, reasoning FROM {self.framework_schema}.plan_cache WHERE signature = %s AND expires_at > NOW()",
                    (sig,),
                )
                row = cur.fetchone()
                if row:
                    return {"plan": row[0], "reasoning": row[1] or ""}
        except Exception:
            pass
        return None

    def _cache_set(self, sig: str, plan: dict):
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.framework_schema}.plan_cache (signature, domain, plan, reasoning, expires_at)
                    VALUES (%s, %s, %s, %s, NOW() + INTERVAL '%s hours')
                    ON CONFLICT (signature) DO UPDATE
                        SET plan = EXCLUDED.plan, reasoning = EXCLUDED.reasoning,
                            expires_at = EXCLUDED.expires_at
                    """,
                    (
                        sig,
                        self.domain or "_common",
                        json.dumps(plan["plan"]),
                        plan["reasoning"],
                        self.cache_ttl_hours,
                    ),
                )
            self.conn.commit()
        except Exception:
            pass
