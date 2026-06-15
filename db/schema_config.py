"""Framework schema configuration. Default is 'data_details', configurable for flexibility."""

import os

DEFAULT_FRAMEWORK_SCHEMA = "data_details"


def get_framework_schema() -> str:
    """Get the framework schema name (default: data_details)."""
    return os.getenv("FRAMEWORK_SCHEMA", DEFAULT_FRAMEWORK_SCHEMA)
