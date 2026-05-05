"""Seeder: Statistics Canada CSD shapefile → municipality boundaries."""

from pathlib import Path
from seeders.base import Seeder


class StatsCanShapefileSeeder(Seeder):
    """Seed municipality_boundaries from Statistics Canada CSD shapefile.

    Disabled by default — requires manual shapefile download from StatsCan.
    Download: https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/index2021-eng.cfm
    File: lcsd000a21a_e.zip → extract .shp
    """

    name = "statscan_shapefile"
    domain = "real_estate"
    target_table = "municipality_boundaries"
    source_tag = "statscan"
    schema_required = ["municipality_boundaries"]

    def fetch(self):
        shapefile_path = self.config.get("shapefile_path")
        if not shapefile_path:
            raise ValueError("shapefile_path not configured in manifest")
        path = Path(shapefile_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Shapefile not found: {path}\n"
                "Download from StatsCan: https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/"
            )
        try:
            import shapefile
        except ImportError:
            raise RuntimeError("pyshp required: pip install pyshp")
        return shapefile.Reader(str(path))

    def parse(self, payload) -> list:
        rows = []
        for shape_rec in payload.shapeRecords():
            rec = shape_rec.record
            rows.append({
                "csd_uid": str(rec["CSDUID"]),
                "name": str(rec["CSDNAME"]),
                "province_code": str(rec["PRUID"]),
                "csd_type": str(rec["CSDTYPE"]),
                "source": self.source_tag,
            })
        return rows

    def upsert(self, conn, rows: list) -> int:
        if not rows:
            return 0
        params = [(r["csd_uid"], r["name"], r["province_code"], r["csd_type"], r["source"]) for r in rows]
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO municipality_boundaries (csd_uid, name, province_code, csd_type, source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (csd_uid) DO NOTHING
                """,
                params,
            )
        conn.commit()
        return len(rows)
