"""Tests for the integration config store (sub-project #1 of the Integration Builder).

Covers loading per-domain/per-source integration YAML, credential resolution by
env-var name (secret never lives in the config file), and the template helper.
"""

import textwrap
from pathlib import Path

import pytest

from integrations.config import (
    AuthConfig,
    Endpoint,
    IntegrationConfig,
    load_integration,
    list_integrations,
    write_integration_template,
)


# ── fixtures ────────────────────────────────────────────────────────────────

def _write_source(root: Path, domain: str, source: str, body: str) -> None:
    d = root / "integrations" / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{source}.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


_VALID = """
    domain: sports_ticketing
    source: nhl_stats
    base_url: https://api-web.nhle.com
    auth:
      type: bearer
      env_var: NHL_API_KEY
    endpoints:
      schedule:
        path: /v1/schedule/{date}
        method: GET
        params:
          expand: schedule.teams
      teams:
        path: /v1/teams
        method: GET
"""


# ── load_integration ──────────────────────────────────────────────────────────

def test_load_parses_valid_config(tmp_path):
    _write_source(tmp_path, "sports_ticketing", "nhl_stats", _VALID)

    cfg = load_integration("sports_ticketing", "nhl_stats", root_dir=tmp_path)

    assert isinstance(cfg, IntegrationConfig)
    assert cfg.domain == "sports_ticketing"
    assert cfg.source == "nhl_stats"
    assert cfg.base_url == "https://api-web.nhle.com"
    assert cfg.auth.type == "bearer"
    assert cfg.auth.env_var == "NHL_API_KEY"


def test_load_parses_endpoints(tmp_path):
    _write_source(tmp_path, "sports_ticketing", "nhl_stats", _VALID)

    cfg = load_integration("sports_ticketing", "nhl_stats", root_dir=tmp_path)

    assert set(cfg.endpoints) == {"schedule", "teams"}
    sched = cfg.endpoints["schedule"]
    assert isinstance(sched, Endpoint)
    assert sched.name == "schedule"
    assert sched.path == "/v1/schedule/{date}"
    assert sched.method == "GET"
    assert sched.params == {"expand": "schedule.teams"}
    # endpoint without params defaults to empty dict
    assert cfg.endpoints["teams"].params == {}


def test_load_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        load_integration("sports_ticketing", "missing", root_dir=tmp_path)
    assert "missing" in str(exc.value)


def test_load_missing_required_key_raises_valueerror(tmp_path):
    _write_source(
        tmp_path, "sports_ticketing", "no_base",
        "domain: sports_ticketing\nsource: no_base\nauth:\n  type: none\n",
    )
    with pytest.raises(ValueError) as exc:
        load_integration("sports_ticketing", "no_base", root_dir=tmp_path)
    assert "base_url" in str(exc.value)


def test_load_invalid_auth_type_raises_valueerror(tmp_path):
    _write_source(
        tmp_path, "sports_ticketing", "bad_auth",
        """
        domain: sports_ticketing
        source: bad_auth
        base_url: https://x.test
        auth:
          type: oauth_magic
          env_var: X_KEY
        endpoints: {}
        """,
    )
    with pytest.raises(ValueError) as exc:
        load_integration("sports_ticketing", "bad_auth", root_dir=tmp_path)
    msg = str(exc.value)
    assert "bearer" in msg and "header" in msg and "query" in msg and "none" in msg


# ── resolve_credential ──────────────────────────────────────────────────────────

def _cfg(auth: AuthConfig) -> IntegrationConfig:
    return IntegrationConfig(
        domain="d", source="s", base_url="https://x.test",
        auth=auth, endpoints={},
    )


def test_resolve_credential_returns_value_when_set(monkeypatch):
    import integrations.config as mod
    monkeypatch.setattr(mod, "get_config_value", lambda k, default=None: "secret-123" if k == "NHL_API_KEY" else default)
    cfg = _cfg(AuthConfig(type="bearer", env_var="NHL_API_KEY", param_name=None))

    assert cfg.resolve_credential() == "secret-123"


def test_resolve_credential_raises_when_env_var_missing(monkeypatch):
    import integrations.config as mod
    monkeypatch.setattr(mod, "get_config_value", lambda k, default=None: default)
    cfg = _cfg(AuthConfig(type="header", env_var="NHL_API_KEY", param_name="X-Api-Key"))

    with pytest.raises(ValueError) as exc:
        cfg.resolve_credential()
    assert "NHL_API_KEY" in str(exc.value)
    assert ".env" in str(exc.value)


def test_resolve_credential_none_auth_returns_none(monkeypatch):
    import integrations.config as mod
    monkeypatch.setattr(mod, "get_config_value", lambda k, default=None: pytest.fail("should not read env"))
    cfg = _cfg(AuthConfig(type="none", env_var=None, param_name=None))

    assert cfg.resolve_credential() is None


@pytest.mark.parametrize("auth_type,param", [("bearer", None), ("header", "X-Api-Key"), ("query", "api_key")])
def test_auth_param_name_semantics(auth_type, param):
    auth = AuthConfig(type=auth_type, env_var="K", param_name=param)
    assert auth.type == auth_type
    assert auth.param_name == param


# ── list_integrations ──────────────────────────────────────────────────────────

def test_list_integrations_returns_source_slugs(tmp_path):
    _write_source(tmp_path, "sports_ticketing", "nhl_stats", _VALID)
    _write_source(tmp_path, "sports_ticketing", "ticketmaster", _VALID)

    sources = list_integrations("sports_ticketing", root_dir=tmp_path)

    assert sorted(sources) == ["nhl_stats", "ticketmaster"]


def test_list_integrations_empty_for_unknown_domain(tmp_path):
    assert list_integrations("no_such_domain", root_dir=tmp_path) == []


# ── write_integration_template ──────────────────────────────────────────────────

def test_write_template_creates_skeleton_and_env_example(tmp_path):
    paths = write_integration_template(
        "sports_ticketing", "nhl_stats", env_var="NHL_API_KEY", root_dir=tmp_path,
    )

    yaml_path = tmp_path / "integrations" / "sports_ticketing" / "nhl_stats.yaml"
    assert yaml_path.exists()
    assert str(yaml_path) in paths
    # the written skeleton is itself loadable
    cfg = load_integration("sports_ticketing", "nhl_stats", root_dir=tmp_path)
    assert cfg.source == "nhl_stats"
    assert cfg.auth.env_var == "NHL_API_KEY"

    env_example = (tmp_path / ".env.example").read_text(encoding="utf-8")
    assert "NHL_API_KEY=" in env_example


def test_write_template_is_idempotent_on_env_example(tmp_path):
    write_integration_template("sports_ticketing", "nhl_stats", env_var="NHL_API_KEY", root_dir=tmp_path)
    write_integration_template("sports_ticketing", "ticketmaster", env_var="NHL_API_KEY", root_dir=tmp_path)

    env_example = (tmp_path / ".env.example").read_text(encoding="utf-8")
    assert env_example.count("NHL_API_KEY=") == 1


def test_write_template_none_auth_skips_env_example(tmp_path):
    """Open APIs (auth.type=none) need no credential and no .env.example entry."""
    paths = write_integration_template(
        "sports_ticketing", "milb_schedule", auth_type="none", root_dir=tmp_path,
    )

    yaml_path = tmp_path / "integrations" / "sports_ticketing" / "milb_schedule.yaml"
    assert paths == [str(yaml_path)]                         # only the yaml, no env file
    assert not (tmp_path / ".env.example").exists()

    cfg = load_integration("sports_ticketing", "milb_schedule", root_dir=tmp_path)
    assert cfg.auth.type == "none"
    assert cfg.auth.env_var is None
    assert cfg.resolve_credential() is None


def test_write_template_credentialed_without_env_var_raises(tmp_path):
    with pytest.raises(ValueError) as exc:
        write_integration_template("sports_ticketing", "x", auth_type="bearer", root_dir=tmp_path)
    assert "env_var" in str(exc.value)
