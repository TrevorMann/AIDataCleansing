"""Domain-agnostic seeder base class."""

from abc import ABC, abstractmethod
from typing import Any, Optional


def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)",
            (table,),
        )
        return cur.fetchone()[0]


class Seeder(ABC):
    """Base class for idempotent public-data seeders. Domain-neutral."""

    name: str = ""
    domain: str = ""
    target_table: str = ""
    source_tag: str = ""
    schema_required: list = []

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    def fetch(self) -> Any:
        """Pull data from public source. Return raw payload."""

    @abstractmethod
    def parse(self, payload: Any) -> list:
        """Parse payload into list of row dicts ready for insert."""

    @abstractmethod
    def upsert(self, conn, rows: list) -> int:
        """Idempotent upsert. Returns number of rows processed."""

    def validate_schema(self, conn):
        for tbl in self.schema_required:
            assert _table_exists(conn, tbl), f"{tbl} missing — run migrations first"

    def run(self, conn) -> int:
        self.validate_schema(conn)
        payload = self.fetch()
        rows = self.parse(payload)
        return self.upsert(conn, rows)
