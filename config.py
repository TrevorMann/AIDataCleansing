import os


def _load_env() -> dict:
    """Load key=value pairs from .env file. Returns dict."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    vals: dict = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    return vals


_ENV_CACHE: dict | None = None


def get_config_value(key: str, default: str | None = None) -> str | None:
    """Read key from .env file, then os.environ, then default."""
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = _load_env()
    return _ENV_CACHE.get(key) or os.environ.get(key) or default


def load_db_path() -> str:
    """Load DB_PATH from .env, then env var, then default to data/cleaning.db."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                if line.startswith('DB_PATH='):
                    db_path = line.split('=', 1)[1].strip()
                    if db_path:
                        db_dir = os.path.dirname(db_path)
                        if db_dir:
                            os.makedirs(db_dir, exist_ok=True)
                        return db_path

    db_path = os.environ.get('DB_PATH', 'data/cleaning.db')
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    return db_path


DB_PATH: str = load_db_path()
