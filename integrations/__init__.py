"""Integration config store — per-domain external source connection details.

Sub-project #1 of the Integration Builder. Holds non-secret connection config
(base URL, auth style, endpoints) in committed YAML; secrets stay in .env and are
referenced by env-var name only. See
docs/superpowers/specs/2026-05-31-integration-config-store-design.md.
"""
