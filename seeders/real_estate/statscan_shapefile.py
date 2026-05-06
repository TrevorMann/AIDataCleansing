"""Seeder: Stats Can lfsa + lcsd shapefiles → fsa_municipality_mapping via spatial join."""

from pathlib import Path
from seeders.base import Seeder


class StatsCanShapefileSeeder(Seeder):
    """
    Spatial join: FSA centroid (lfsa shapefile) within CSD polygon (lcsd shapefile)
    → normalized_municipality.

    Config keys:
      fsa_shapefile   path to lfsa...shp  (FSA digital boundary file)
      csd_shapefile   path to lcsd...shp  (Census Subdivision boundary file)
      country         default "CA"
      province        optional filter — only seed FSAs for this PRUID (e.g. "35" = ON)

    Stats Can download:
      FSA: https://www12.statcan.gc.ca/census-recensement/2021/geo/sip-pis/boundary-limites/
           → Forward Sortation Area → Digital boundary → lfsa000a21a_e.zip
      CSD: same page → Census Subdivision → Digital boundary → lcsd000aXXa_e.zip
    """

    name = "statscan_shapefile"
    domain = "real_estate"
    target_table = "fsa_municipality_mapping"
    source_tag = "statscan"
    schema_required = ["fsa_municipality_mapping"]

    # Stats Can PRUID → ISO province code
    _PRUID_TO_PROVINCE = {
        "10": "NL", "11": "PE", "12": "NS", "13": "NB",
        "24": "QC", "35": "ON", "46": "MB", "47": "SK",
        "48": "AB", "59": "BC", "60": "YT", "61": "NT", "62": "NU",
    }

    def fetch(self) -> dict:
        fsa_path = self.config.get("fsa_shapefile")
        csd_path = self.config.get("csd_shapefile")
        if not fsa_path or not csd_path:
            raise ValueError("fsa_shapefile and csd_shapefile required in config")
        for p in (fsa_path, csd_path):
            if not Path(p).exists():
                raise FileNotFoundError(f"Shapefile not found: {p}")
        try:
            import shapefile
            from shapely.geometry import Point, shape
        except ImportError:
            raise RuntimeError("pip install pyshp shapely")

        province_filter = self.config.get("province")  # PRUID string e.g. "35"

        # Load CSD polygons into memory: {csd_uid: (polygon, csdname, pruid)}
        csd_reader = shapefile.Reader(csd_path, encoding="latin-1")
        csd_fields = [f[0] for f in csd_reader.fields[1:]]
        csds = []
        for sr in csd_reader.shapeRecords():
            rec = dict(zip(csd_fields, sr.record))
            poly = shape(sr.shape.__geo_interface__)
            csds.append((poly, rec["CSDNAME"], str(rec["PRUID"])))

        # Load FSA centroids
        fsa_reader = shapefile.Reader(fsa_path, encoding="latin-1")
        fsa_fields = [f[0] for f in fsa_reader.fields[1:]]
        fsa_records = []
        for sr in fsa_reader.shapeRecords():
            rec = dict(zip(fsa_fields, sr.record))
            pruid = str(rec["PRUID"])
            if province_filter and pruid != province_filter:
                continue
            centroid = shape(sr.shape.__geo_interface__).centroid
            fsa_records.append({
                "fsa": rec["CFSAUID"],
                "pruid": pruid,
                "centroid": centroid,
            })

        return {"fsa_records": fsa_records, "csds": csds}

    def parse(self, payload: dict) -> list:
        from shapely.geometry import Point

        country = self.config.get("country", "CA")
        fsa_records = payload["fsa_records"]
        csds = payload["csds"]

        rows = []
        unmatched = 0
        for fsa in fsa_records:
            pt = fsa["centroid"]
            municipality = None
            for poly, csdname, pruid in csds:
                if poly.contains(pt):
                    municipality = csdname
                    break
            if not municipality:
                unmatched += 1
                continue
            province = self._PRUID_TO_PROVINCE.get(fsa["pruid"], fsa["pruid"])
            rows.append({
                "fsa": fsa["fsa"],
                "province": province,
                "country": country,
                "normalized_municipality": municipality,
                "source": self.source_tag,
            })

        if unmatched:
            print(f"  [{self.name}] {unmatched} FSA centroids unmatched (border/water FSAs)")
        return rows

    def upsert(self, conn, rows: list) -> int:
        from db.upsert import bulk_insert_ignore
        import datetime
        today = datetime.date.today().isoformat()
        enriched = [{**r, "valid_from": today} for r in rows]
        return bulk_insert_ignore(
            conn, "fsa_municipality_mapping", enriched,
            ["fsa", "province", "country"],
        )
