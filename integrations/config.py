"""Loader for per-domain/per-source integration configs.

Reads ``integrations/<domain>/<source>.yaml`` into typed pydantic models and
resolves the credential by env-var name via ``config.get_config_value`` (so the
secret value never lives in the committed config file).
"""

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

from config import get_config_value


AuthType = Literal["bearer", "header", "query", "none"]


class AuthConfig(BaseModel):
    """How a source authenticates. ``env_var`` names the .env key holding the secret."""

    type: AuthType
    env_var: Optional[str] = None
    param_name: Optional[str] = None  # header name (type=header) or query key (type=query)


class Endpoint(BaseModel):
    """A single configured endpoint. Stored only — not called by this sub-project."""

    name: str = ""
    path: str
    method: str = "GET"
    params: dict = Field(default_factory=dict)


class IntegrationConfig(BaseModel):
    domain: str
    source: str
    base_url: str
    auth: AuthConfig
    endpoints: dict[str, Endpoint] = Field(default_factory=dict)

    def resolve_credential(self) -> Optional[str]:
        """Resolve the secret from .env/environ. None for auth.type == 'none'.

        Raises ValueError naming the env var if it is configured but unset.
        """
        if self.auth.type == "none":
            return None
        value = get_config_value(self.auth.env_var) if self.auth.env_var else None
        if not value:
            raise ValueError(
                f"Credential env var '{self.auth.env_var}' is not set — add it to your .env file."
            )
        return value


# ── helpers ───────────────────────────────────────────────────────────────────

def _root(root_dir: Optional[Path]) -> Path:
    return Path(root_dir) if root_dir else Path(__file__).resolve().parent.parent


def _integrations_dir(root_dir: Optional[Path]) -> Path:
    return _root(root_dir) / "integrations"


# ── public API ──────────────────────────────────────────────────────────────────

def load_integration(domain: str, source: str, root_dir: Optional[Path] = None) -> IntegrationConfig:
    path = _integrations_dir(root_dir) / domain / f"{source}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No integration config for source '{source}' at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Integration config at {path} must be a mapping")

    # Inject the endpoint name from its map key before validation.
    raw = dict(raw)
    endpoints = raw.get("endpoints") or {}
    raw["endpoints"] = {
        name: {**(spec or {}), "name": name} for name, spec in endpoints.items()
    }

    try:
        return IntegrationConfig(**raw)
    except ValidationError as e:
        raise ValueError(str(e)) from e


def list_integrations(domain: str, root_dir: Optional[Path] = None) -> list[str]:
    d = _integrations_dir(root_dir) / domain
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


_SKELETON_HEAD = """\
domain: {domain}
source: {source}
base_url: https://CHANGE-ME.example.com
"""

_AUTH_NONE = """\
auth:
  type: none            # open API — no credential
"""

_AUTH_CRED = """\
auth:
  type: {auth_type}     # bearer | header | query | none
  env_var: {env_var}    # secret value goes in .env, never here
  # param_name: X-Api-Key   # header name (type=header) or query key (type=query)
"""

_SKELETON_ENDPOINTS = """\
endpoints:
  example:
    path: /v1/example
    method: GET
"""


def write_integration_template(
    domain: str,
    source: str,
    env_var: Optional[str] = None,
    auth_type: AuthType = "bearer",
    root_dir: Optional[Path] = None,
) -> list[str]:
    """Write a commented config skeleton; append an .env.example placeholder unless open.

    For ``auth_type="none"`` (open APIs) no credential is needed: the skeleton omits
    ``env_var`` and nothing is written to ``.env.example``. Credentialed types require
    ``env_var``. Idempotent: an existing config file is left untouched and the
    ``.env.example`` placeholder is added at most once. Returns the paths written/touched.
    """
    if auth_type != "none" and not env_var:
        raise ValueError(f"auth_type='{auth_type}' requires an env_var name for the credential")

    if auth_type == "none":
        auth_block = _AUTH_NONE
    else:
        auth_block = _AUTH_CRED.format(auth_type=auth_type, env_var=env_var)
    skeleton = (
        _SKELETON_HEAD.format(domain=domain, source=source)
        + auth_block
        + _SKELETON_ENDPOINTS
    )

    root = _root(root_dir)
    target_dir = root / "integrations" / domain
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    yaml_path = target_dir / f"{source}.yaml"
    if not yaml_path.exists():
        yaml_path.write_text(skeleton, encoding="utf-8")
    written.append(str(yaml_path))

    if auth_type == "none":
        return written

    env_example = root / ".env.example"
    placeholder = f"{env_var}="
    existing = env_example.read_text(encoding="utf-8") if env_example.exists() else ""
    if placeholder not in existing:
        with env_example.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"{placeholder}your-value-here\n")
        written.append(str(env_example))

    return written
