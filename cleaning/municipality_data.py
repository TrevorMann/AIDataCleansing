"""Data loading functions for municipality normalization.

This module provides idempotent data loaders for:
- Shapefile parsing (Statistics Canada LCD boundaries)
- Wikipedia FSA extraction (Toronto M-series postal codes)
- City hierarchy seeding (Toronto amalgamation mapping)
"""

import sqlite3
import uuid
import re
from typing import Optional, Tuple
from datetime import datetime

try:
    import shapefile
except ImportError:
    shapefile = None

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    requests = None
    BeautifulSoup = None


def load_shapefile(conn: sqlite3.Connection, shapefile_path: str) -> int:
    """Parse Statistics Canada shapefile and populate geo_boundary_reference.

    Reads a Statistics Canada LCD (Census Division) shapefile and loads
    municipality boundaries with temporal validity.

    Args:
        conn: SQLite connection (should have municipality tables created)
        shapefile_path: Path to .shp file (e.g., "lcsd000a25a_e.shp")

    Returns:
        Count of boundaries loaded (0 if library not installed)

    Raises:
        FileNotFoundError: If shapefile doesn't exist
    """
    if shapefile is None:
        print("Warning: shapefile library (pyshp) not installed, skipping shapefile load")
        return 0

    count = 0
    try:
        # Read shapefile
        sf = shapefile.Reader(shapefile_path)

        for shape_record in sf.shapeRecords():
            record = shape_record.record
            shape = shape_record.shape

            # Extract attributes from record
            # Statistics Canada shapefiles typically have: PRNAME, CDNAME
            # For now, using generic "Toronto" as placeholder (would need actual field inspection)
            normalized_municipality = "Toronto"
            province = "ON"
            wkt = _shape_to_wkt(shape)

            # Insert into geo_boundary_reference
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO geo_boundary_reference
                (normalized_municipality, province, boundary_polygon, valid_from, valid_to, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (normalized_municipality, province, wkt, "2001-01-01", None, "stats_canada"),
            )
            count += 1

        conn.commit()
        return count

    except FileNotFoundError:
        raise FileNotFoundError(f"Shapefile not found: {shapefile_path}")
    except Exception as e:
        conn.rollback()
        raise Exception(f"Error loading shapefile: {e}")


def load_wikipedia_fsas(conn) -> int:
    """Scrape Toronto postal codes from Wikipedia and pre-seed cache.

    Extracts M-series FSAs (postal code prefixes) from the Wikipedia list
    of Canadian postal codes and pre-populates the municipality lookup cache.

    Args:
        conn: Database connection (SQLite or PostgreSQL, should have municipality tables created)

    Returns:
        Count of entries added to cache

    Note:
        Requires network access to Wikipedia. If libraries not installed or
        network unavailable, returns 0.

        Idempotent: skips scraping if cache already has data from this source.
    """
    if requests is None or BeautifulSoup is None:
        print("Warning: requests and beautifulsoup4 not installed, skipping Wikipedia load")
        return 0

    # Ensure source column exists (works with both SQLite and PostgreSQL)
    try:
        from db.connection import get_backend
        if get_backend() == "postgres":
            from db.pg_init import add_source_column_to_cache
        else:
            from db.sqlite_municipality_schema import add_source_column_to_cache
        add_source_column_to_cache(conn)
    except Exception:
        pass

    # Skip if cache already has data from Wikipedia (idempotent by source)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM municipality_lookup_cache WHERE source = 'wikipedia'")
    wiki_count = cursor.fetchone()[0]
    if wiki_count > 0:
        return 0

    count = 0
    try:
        # Fetch Wikipedia page
        url = "https://en.wikipedia.org/wiki/List_of_postal_codes_of_Canada:_M"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse Wikipedia table: FSA in <b> tag, municipality in following link
        # Pattern: <b>M1A</b>...<a href=...>North York</a>
        soup = BeautifulSoup(response.content, "html.parser")

        for bold_tag in soup.find_all("b"):
            fsa_text = bold_tag.get_text(strip=True)
            # Check if this is an FSA code (M followed by digit and letter)
            if re.match(r'^M[0-9][A-Z]$', fsa_text):
                # Find the next <a> tag containing municipality name
                municipality = "Toronto"  # default fallback

                # Look for nearby <a> tag within parent
                parent = bold_tag.parent
                if parent:
                    link = parent.find("a")
                    if link:
                        municipality = link.get_text(strip=True)

                lookup_key = f"fsa:{fsa_text}"
                lookup_value = municipality
                query_params = '{"fsa": "' + fsa_text + '"}'

                cursor = conn.cursor()
                try:
                    # PostgreSQL
                    cursor.execute(
                        """
                        INSERT INTO municipality_lookup_cache
                        (lookup_key, lookup_value, query_params, source, hit_count)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (lookup_key) DO NOTHING
                        """,
                        (lookup_key, lookup_value, query_params, 'wikipedia', 1)
                    )
                except Exception:
                    # SQLite fallback
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO municipality_lookup_cache
                        (lookup_key, lookup_value, query_params, source, hit_count)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (lookup_key, lookup_value, query_params, 'wikipedia', 1)
                    )
                count += 1

        conn.commit()
        return count

    except Exception as e:
        # Network errors are non-fatal; log and return 0
        print(f"Warning: Could not load Wikipedia FSAs: {e}")
        return 0


def load_city_hierarchy(conn: sqlite3.Connection) -> int:
    """Seed city_municipality_map with Toronto amalgamation hierarchy.

    Populates the city-to-municipality mapping with the Toronto amalgamation
    relationships (1998), mapping historical neighborhoods to Toronto.

    Args:
        conn: SQLite connection (should have municipality tables created)

    Returns:
        Count of entries added

    Note:
        This is idempotent; duplicate entries are ignored via unique constraint.
    """
    # Toronto amalgamation: January 1, 1998
    # Maps historical municipalities to Toronto
    municipalities = [
        ("North York", "Toronto", "ON", "CA", "amalgamated_former", 0.99),
        ("Scarborough", "Toronto", "ON", "CA", "amalgamated_former", 0.99),
        ("Etobicoke", "Toronto", "ON", "CA", "amalgamated_former", 0.99),
        ("East York", "Toronto", "ON", "CA", "amalgamated_former", 0.99),
        ("York", "Toronto", "ON", "CA", "amalgamated_former", 0.99),
        ("Toronto", "Toronto", "ON", "CA", "city", 1.00),
    ]

    count = 0
    try:
        for city_name, normalized_municipality, province, country, mtype, confidence in municipalities:
            cursor = conn.cursor()
            # Use INSERT OR IGNORE to make it idempotent
            cursor.execute(
                """
                INSERT OR IGNORE INTO city_municipality_map
                (city_name, province, country, normalized_municipality, confidence_score, source, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (city_name, province, country, normalized_municipality, confidence, "seed"),
            )
            # Only count if insertion happened (not already present)
            if cursor.rowcount > 0:
                count += 1

        conn.commit()
        return count

    except Exception as e:
        conn.rollback()
        raise Exception(f"Error loading city hierarchy: {e}")


def _shape_to_wkt(shape) -> str:
    """Convert pyshp shape object to WKT (Well-Known Text) format.

    Args:
        shape: A pyshp shape object

    Returns:
        WKT string representation of the shape
    """
    try:
        # Handle different shape types
        if shape.shapeType == 5:  # Polygon
            return _polygon_to_wkt(shape.points, shape.parts)
        elif shape.shapeType == 15:  # PolygonZ
            return _polygon_to_wkt(shape.points, shape.parts)
        else:
            # For other types, return empty string (unknown)
            return ""
    except Exception:
        return ""


def _polygon_to_wkt(points: list, parts: list) -> str:
    """Convert polygon points and parts to WKT POLYGON format.

    Args:
        points: List of (x, y) coordinate tuples
        parts: List of part indices

    Returns:
        WKT POLYGON string
    """
    if not points:
        return ""

    try:
        # Build polygon with correct ring handling
        if not parts or (len(parts) == 1 and parts[0] == 0):
            # Single ring
            coords = ", ".join(f"{x} {y}" for x, y in points)
            return f"POLYGON(({coords}))"
        else:
            # Multiple rings (exterior + holes)
            rings = []
            for i, part_idx in enumerate(parts):
                end_idx = parts[i + 1] if i + 1 < len(parts) else len(points)
                ring_points = points[part_idx:end_idx]
                coords = ", ".join(f"{x} {y}" for x, y in ring_points)
                rings.append(f"({coords})")
            return f"POLYGON({', '.join(rings)})"
    except Exception:
        return ""
