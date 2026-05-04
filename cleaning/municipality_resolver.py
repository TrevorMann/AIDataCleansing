import uuid
from typing import Optional, Tuple, Dict
from cleaning.address_standardizer import standardize_address
from cleaning.confidence_scorer import ConfidenceScorer
from cleaning.nominatim_client import NominatimClient


class MunicipalityResolver:
    """5-layer municipality resolution pipeline."""

    def __init__(self, conn):
        """Initialize resolver with database connection.

        Args:
            conn: sqlite3 connection object
        """
        self.conn = conn

    def resolve(self, listing: Dict, verbose: bool = False) -> Dict:
        """
        Main entry point. Resolve listing through 5-layer pipeline.

        Input listing dict must have:
        - street, municipality, postal_code, province, year

        Output listing dict includes:
        - normalized_municipality, municipality_ref_id, confidence_score, normalization_status
        """
        street_normalized = standardize_address(listing.get("street", ""))
        fsa = listing.get("postal_code", "")[:3] if listing.get("postal_code") else ""
        province = listing.get("province", "ON").upper()
        listing_year = listing.get("year", 2024)
        upstream_municipality = listing.get("municipality", "")

        address_display = f"{listing.get('street', '')} ({upstream_municipality})"
        if verbose:
            print(f"  Resolving: {address_display}...", end=" ", flush=True)

        # Layer 1: Cache lookup
        if verbose:
            print("cache...", end=" ", flush=True)
        cache_result = self._cache_lookup(upstream_municipality, street_normalized, fsa, province)
        if cache_result:
            normalized, ref_id, confidence = cache_result
            listing["normalized_municipality"] = normalized
            listing["municipality_ref_id"] = ref_id
            listing["confidence_score"] = confidence
            listing["normalization_status"] = "completed"
            if verbose:
                print(f"✓ ({normalized})")
            return listing

        # Layer 2: Boundary lookup
        if verbose:
            print("boundary...", end=" ", flush=True)
        boundary_result = self._boundary_lookup(fsa, province, listing_year)
        if boundary_result:
            normalized, ref_id, confidence = boundary_result

            # Check if confidence is low and upstream is unknown/suspicious
            if confidence < 0.60 and (not upstream_municipality or upstream_municipality.lower() in ['unknown', 'n/a', 'pending']):
                # Low confidence with unknown upstream, escalate to Layer 3
                result = self._nominatim_validate(listing, street_normalized, fsa, province, listing_year)
                if result:
                    if isinstance(result, tuple):
                        # Handle tuple return (normalized_municipality, confidence_score)
                        normalized, confidence = result
                        listing["normalized_municipality"] = normalized
                        listing["municipality_ref_id"] = ref_id
                        listing["confidence_score"] = confidence
                        listing["normalization_status"] = "completed"
                    else:
                        # Handle dict return
                        listing.update(result)
                    return listing

            # Confidence is acceptable or upstream is known, complete with boundary result
            listing["normalized_municipality"] = normalized
            listing["municipality_ref_id"] = ref_id
            listing["confidence_score"] = confidence
            listing["normalization_status"] = "completed"
            if verbose:
                print(f"✓ ({normalized})")
            return listing

        # Layer 3: Nominatim validation (if no boundary match)
        if verbose:
            print("nominatim...", end=" ", flush=True)
        result = self._nominatim_validate(listing, street_normalized, fsa, province, listing_year)
        if result:
            if isinstance(result, tuple):
                # Handle tuple return (normalized_municipality, confidence_score)
                normalized, confidence = result
                listing["normalized_municipality"] = normalized
                listing["municipality_ref_id"] = None  # No ref_id from Nominatim
                listing["confidence_score"] = confidence
                listing["normalization_status"] = "completed"
            else:
                # Handle dict return
                listing.update(result)
            if verbose:
                status = listing.get("normalization_status", "?")
                print(f"✓ ({listing.get('normalized_municipality', '?')}, {status})")
            return listing

        # Layer 4: Agent escalation (if score < 0.60)
        if verbose:
            print("agent...", end=" ", flush=True)
        result = self._agent_escalate(listing, street_normalized, fsa, province, listing_year)
        if result:
            listing.update(result)
            if verbose:
                print(f"✓ (escalated)")
            return listing

        # Layer 5: Flag for human review
        listing["normalized_municipality"] = None
        listing["municipality_ref_id"] = None
        listing["confidence_score"] = 0.0
        listing["normalization_status"] = "review"
        if verbose:
            print("⚠ (manual review)")
        return listing

    def _cache_lookup(self, upstream_municipality: str, street_normalized: str,
                      fsa: str, province: str) -> Optional[Tuple[str, str, float]]:
        """Layer 1: Query cache for exact match.

        Args:
            upstream_municipality: Original municipality name from input
            street_normalized: Standardized street address
            fsa: Postal code prefix (first 3 characters)
            province: Province/state code

        Returns:
            Tuple of (normalized_municipality, municipality_ref_id, confidence_score) or None
        """
        if not fsa:
            return None

        # Try FSA-only lookup first (most common cache pattern)
        lookup_key = f"fsa:{fsa}"
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT lookup_value FROM municipality_lookup_cache
            WHERE lookup_key = ?
        """, (lookup_key,))
        row = cursor.fetchone()
        if row:
            # FSA cache hit: return (municipality, dummy_ref_id, high_confidence)
            return (row[0], "cache-hit", 0.95)

        return None

    def _boundary_lookup(self, fsa: str, province: str, listing_year: int) -> Optional[Tuple[str, str, float]]:
        """Layer 2: Query shapefile boundary via FSA.

        Args:
            fsa: Postal code prefix (first 3 characters)
            province: Province/state code
            listing_year: Year of the listing

        Returns:
            Tuple of (normalized_municipality, municipality_ref_id, confidence_score) or None
        """
        # This method would query the geo_boundary_reference and fsa_municipality_mapping tables
        # Default implementation returns None (no boundary match)
        return None

    def _nominatim_validate(self, listing: Dict, street_normalized: str,
                           fsa: str, province: str, listing_year: int) -> Optional[Dict]:
        """Layer 3: Nominatim validation + confidence scoring.

        Args:
            listing: Original listing dict
            street_normalized: Standardized street address
            fsa: Postal code prefix
            province: Province/state code
            listing_year: Year of the listing

        Returns:
            Dict with normalized_municipality, municipality_ref_id, confidence_score, normalization_status
            or None if validation fails
        """
        # Assume Layer 2 found a boundary candidate
        boundary_result = self._boundary_lookup(fsa, province, listing_year)
        if not boundary_result:
            return None

        normalized, ref_id, _ = boundary_result

        scorer = ConfidenceScorer()
        scorer.add_fsa_signal()  # 0.35

        # Query Nominatim for city_district validation
        client = NominatimClient(rate_limit_per_sec=1)
        street = listing.get("street", "")
        city_hint = listing.get("municipality", "Toronto")
        postal_code = listing.get("postal_code", "")

        nominatim_results = client.search_structured(
            street=street,
            city=city_hint,
            postalcode=postal_code,
            country="Canada"
        )

        if nominatim_results:
            first_result = nominatim_results[0]
            nominatim_city_district = first_result.get("city_district", "")

            # Cross-check: does Nominatim match our boundary?
            if nominatim_city_district and nominatim_city_district.lower() == normalized.lower():
                # Match! Nominatim provides geographic validation (like polygon match)
                scorer.add_polygon_match()  # +0.35 (geocoded point validates boundary)
                scorer.add_upstream_match()  # +0.20
                scorer.add_street_consistency()  # +0.10
            elif nominatim_city_district:
                # Conflict: Nominatim says something else
                scorer.apply_nominatim_conflict_penalty()  # -0.25

        confidence = scorer.get_score()
        routing = scorer.route()

        if routing == "auto_complete":
            self._write_cache_entry(listing.get("municipality", ""), street_normalized, fsa, province,
                                   normalized, ref_id, confidence, "nominatim", "completed")
            return {
                "normalized_municipality": normalized,
                "municipality_ref_id": ref_id,
                "confidence_score": confidence,
                "normalization_status": "completed",
            }
        elif routing == "agent_review":
            return {
                "normalized_municipality": normalized,
                "municipality_ref_id": ref_id,
                "confidence_score": confidence,
                "normalization_status": "review",  # Escalate to agent
            }
        else:  # human_review
            return {
                "normalized_municipality": normalized,
                "municipality_ref_id": ref_id,
                "confidence_score": confidence,
                "normalization_status": "review",  # Escalate to human
            }

    def _agent_escalate(self, listing: Dict, street_normalized: str,
                       fsa: str, province: str, listing_year: int) -> Optional[Dict]:
        """Layer 4: Agent escalation (placeholder, implemented in next task).

        Args:
            listing: Original listing dict
            street_normalized: Standardized street address
            fsa: Postal code prefix
            province: Province/state code
            listing_year: Year of the listing

        Returns:
            Dict with normalized_municipality, municipality_ref_id, confidence_score, normalization_status
            or None if escalation fails
        """
        return None

    def _write_cache_entry(self, upstream_municipality: str, street_normalized: str,
                          fsa: str, province: str, normalized_municipality: str,
                          municipality_ref_id: str, confidence_score: float,
                          resolution_source: str, status: str):
        """Write resolved entry to cache.

        Args:
            upstream_municipality: Original municipality name
            street_normalized: Standardized street address
            fsa: Postal code prefix
            province: Province/state code
            normalized_municipality: Canonical municipality name
            municipality_ref_id: Reference ID to geo_boundary_reference
            confidence_score: Confidence of the resolution
            resolution_source: Source of the resolution (e.g., "boundary_table")
            status: Status of normalization
        """
        # This method would insert into the municipality_lookup_cache table
        pass
