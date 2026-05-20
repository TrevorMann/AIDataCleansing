#!/usr/bin/env python3
"""
Data Cleaning Agent - Orchestrates full cleaning workflow:
1. User asks question
2. Claude interprets query and fetches relevant data
3. Data is validated and presented
4. Claude cleans the data
5. Results saved to database
"""

import os
import sys
import json
import re
from datetime import datetime
from db.sqlite_init import init_db, get_db_connection
from db.sqlite_helpers import get_all_raw_data, insert_cleaned_data, insert_audit_log
from db.schema_discovery import get_all_schemas
from validate_data_quality import get_records_needing_cleaning, check_record_quality
from config import DB_PATH

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

init_db(DB_PATH)


class DataCleaningAgent:
    """Agent that handles data cleaning workflow orchestration."""

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.current_batch = []
        self.cleaned_results = []

    def interpret_user_query(self, user_query: str) -> dict:
        """Parse user query for scope/limit/issue-type modifiers.

        Country filtering is handled by ScopeInterpreter (LLM-driven).
        This method only extracts structural query modifiers.
        """
        query_lower = user_query.lower()
        filters = {}

        # Issue type detection
        if 'phone' in query_lower:
            filters['issue_type'] = 'phone'
        if 'postal' in query_lower or 'zip' in query_lower:
            filters['issue_type'] = 'postal'
        if 'name' in query_lower or 'case' in query_lower:
            filters['issue_type'] = 'case'

        # Scope detection
        if 'all' in query_lower or 'uncleaned' in query_lower or 'dirty' in query_lower:
            filters['scope'] = 'all_uncleaned'
        elif 'first' in query_lower:
            filters['scope'] = 'first_batch'
            filters['limit'] = 5

        return filters

    def fetch_data_for_query(self, filters: dict) -> list:
        """Fetch data from database based on interpreted filters."""
        all_records = get_records_needing_cleaning()

        # Filter by country
        if 'country' in filters:
            countries = filters['country'] if isinstance(filters['country'], list) else [filters['country']]

            # Comprehensive country code to all variations mapping (case-insensitive)
            country_map = {
                'CA': ['CA', 'Canada', 'Canadian', 'cdn'],
                'USA': ['USA', 'United States', 'US', 'U.S.', 'U.S.A.', 'America', 'American', 'United States of America'],
                'Mexico': ['MX', 'Mexico', 'Mexican', 'México'],
                'JP': ['JP', 'Japan', 'Japanese', 'Nippon'],
                'NL': ['NL', 'Netherlands', 'Dutch', 'Holland', 'Holland Kingdom', 'The Netherlands'],
            }

            # Flatten all acceptable country values (normalized to uppercase)
            acceptable = set()
            for country in countries:
                country_normalized = country.strip().upper()

                # Check all country mappings
                for code, variations in country_map.items():
                    # Check if the filter matches any variation
                    if any(country_normalized == var.strip().upper() for var in variations):
                        # Add all variations for this country
                        acceptable.update([var.strip().upper() for var in variations])
                        break

            # Filter records (case-insensitive comparison)
            all_records = [r for r in all_records if r['country'].strip().upper() in acceptable]

        # Filter by issue type
        if 'issue_type' in filters:
            issue = filters['issue_type']
            all_records = [r for r in all_records if issue in r.get('_issues', [])]

        # Apply limit
        if 'limit' in filters:
            all_records = all_records[:filters['limit']]

        self.current_batch = all_records
        return all_records

    def format_batch_for_claude(self, records: list) -> str:
        """Format batch as readable table for Claude."""
        if not records:
            return "No records found matching the query."

        headers = ['ID', 'Name', 'Age', 'City', 'Address', 'Postal Code', 'Municipality', 'State/Prov', 'Country', 'Phone']
        col_widths = [4, 15, 5, 12, 25, 12, 15, 10, 12, 15]

        table = ""
        header_row = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
        table += header_row + "\n"
        table += "-" * 155 + "\n"

        for record in records:
            row = [
                str(record['id']),
                record['name'][:15],
                str(record['age']) if record['age'] else '',
                record['city'][:12],
                record['address'][:25],
                record['postal_code'][:12],
                'N/A',  # Municipality - to be filled by Claude
                record['state_province'][:10],
                record['country'][:12],
                record['phone'][:15]
            ]
            row_str = " | ".join(str(v).ljust(w) for v, w in zip(row, col_widths))
            table += row_str + "\n"

        return table

    def parse_cleaned_response(self, claude_response: str) -> list:
        """Parse Claude's cleaned data response into structured format.

        Handles markdown tables, pipe-delimited tables, and structured text.
        """
        lines = claude_response.strip().split('\n')
        cleaned_records = []
        in_table = False
        table_found = False

        # First pass: find and parse table
        for i, line in enumerate(lines):
            # Skip empty lines
            if not line.strip():
                continue

            # Check if this is a table header line
            if 'ID' in line and '|' in line and ('Name' in line or 'Age' in line):
                in_table = True
                table_found = True
                continue

            # Skip separator lines (dashes)
            if '---' in line and '|' in line:
                continue

            # Parse data rows from table
            if in_table and '|' in line and '--' not in line:  # Changed: avoid separator lines
                # Split by pipe and clean
                parts = [p.strip() for p in line.split('|')]
                # Remove empty first/last elements from leading/trailing pipes
                parts = [p for p in parts if p]

                # Need at least 9 parts for: ID, Name, Age, City, Address, Postal, State, Country, Phone
                if len(parts) >= 9 or len(parts) >= 6:  # Be more lenient, require at least 6
                    try:
                        id_str = parts[0]
                        if not id_str.isdigit():
                            in_table = False  # End of table
                            continue

                        # Handle different field counts - take what we have
                        # Expected order: ID, Name, Age, City, Address, Postal Code, Municipality, State/Prov, Country, Phone, Notes
                        record = {
                            'raw_data_id': int(id_str),
                            'name': parts[1] if len(parts) > 1 else '',
                            'age': int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
                            'city': parts[3] if len(parts) > 3 else '',
                            'address': parts[4] if len(parts) > 4 else '',
                            'postal_code': parts[5] if len(parts) > 5 else '',
                            'municipality': parts[6] if len(parts) > 6 else 'N/A',  # Municipality is now column 6
                            'state_province': parts[7] if len(parts) > 7 else 'N/A',
                            'country': parts[8] if len(parts) > 8 else '',
                            'phone': parts[9] if len(parts) > 9 else '',
                            'validation_notes': parts[10] if len(parts) > 10 else '',
                            'cleaned_by': 'claude-assistant'
                        }
                        cleaned_records.append(record)
                    except (ValueError, IndexError) as e:
                        continue

        # If no table found, try to extract from structured text
        if not cleaned_records:
            if not table_found:
                # Structured format with before/after
                cleaned_records = self._parse_structured_response(claude_response)
            else:
                # Table was found but couldn't parse rows
                cleaned_records = self._parse_structured_response(claude_response)

        self.cleaned_results = cleaned_records
        return cleaned_records

    def _parse_structured_response(self, response: str) -> list:
        """Parse Claude's structured text response with before/after pairs."""
        records = []
        lines = response.split('\n')
        current_record = None

        for line in lines:
            # Look for record markers like "Record X:" or "ID X:"
            if 'Record ' in line or ('ID ' in line and ':' in line):
                # Try to extract ID
                match = re.search(r'(?:Record|ID)\s+(\d+)', line)
                if match:
                    if current_record and current_record.get('raw_data_id'):
                        records.append(current_record)
                    current_record = {
                        'raw_data_id': int(match.group(1)),
                        'name': '',
                        'age': None,
                        'city': '',
                        'address': '',
                        'postal_code': '',
                        'municipality': 'N/A',
                        'state_province': 'N/A',
                        'country': '',
                        'phone': '',
                        'validation_notes': '',
                        'cleaned_by': 'claude-assistant'
                    }

            # Look for transformations in the line
            if current_record and ' → ' in line:
                # Extract field name and values
                line_lower = line.lower()
                parts = line.split(' → ')

                if len(parts) == 2:
                    after_value = parts[1].strip().strip('"').strip('(').split('(')[0].strip()

                    # Check what field this is by looking at the beginning of the line
                    if line_lower.startswith('name:') or 'name:' in line_lower[:20]:
                        current_record['name'] = after_value
                    elif line_lower.startswith('age:') or 'age:' in line_lower[:20]:
                        current_record['age'] = int(after_value) if after_value.isdigit() else None
                    elif line_lower.startswith('city:') or 'city:' in line_lower[:20]:
                        current_record['city'] = after_value
                    elif line_lower.startswith('address:') or 'address:' in line_lower[:20]:
                        current_record['address'] = after_value
                    elif line_lower.startswith('postal') or 'postal code:' in line_lower[:30]:
                        current_record['postal_code'] = after_value
                    elif line_lower.startswith('municipality') or 'municipality:' in line_lower[:30]:
                        current_record['municipality'] = after_value
                    elif line_lower.startswith('state') or 'state/prov' in line_lower[:30]:
                        current_record['state_province'] = after_value
                    elif line_lower.startswith('country:') or 'country:' in line_lower[:20]:
                        current_record['country'] = after_value
                    elif line_lower.startswith('phone:') or 'phone:' in line_lower[:20]:
                        current_record['phone'] = after_value
                    elif 'validation' in line_lower or 'notes:' in line_lower:
                        current_record['validation_notes'] = after_value

        # Add last record if exists
        if current_record and current_record.get('raw_data_id'):
            records.append(current_record)

        return records

    def save_cleaned_results(self) -> int:
        """Save all cleaned results to database."""
        if not self.cleaned_results:
            return 0

        batch_index = {r['id']: r for r in self.current_batch}

        saved_count = 0
        for cleaned_data in self.cleaned_results:
            raw_data_id = cleaned_data['raw_data_id']

            # Find original record to detect transformations
            original = batch_index.get(raw_data_id)
            transformations = []

            if original:
                # Detect what changed
                if original['name'] != cleaned_data['name']:
                    transformations.append({
                        'rule': 'name_standardization',
                        'description': f"{original['name']} → {cleaned_data['name']}"
                    })
                if original['phone'] != cleaned_data['phone']:
                    transformations.append({
                        'rule': 'phone_formatting',
                        'description': f"{original['phone']} → {cleaned_data['phone']}"
                    })
                if original['postal_code'] != cleaned_data['postal_code']:
                    transformations.append({
                        'rule': 'postal_code_formatting',
                        'description': f"{original['postal_code']} → {cleaned_data['postal_code']}"
                    })
                if original['country'] != cleaned_data['country']:
                    transformations.append({
                        'rule': 'country_expansion',
                        'description': f"{original['country']} → {cleaned_data['country']}"
                    })
                if original['state_province'] != cleaned_data['state_province']:
                    transformations.append({
                        'rule': 'state_expansion',
                        'description': f"{original['state_province']} → {cleaned_data['state_province']}"
                    })

            # Save to database
            cleaned_id = insert_cleaned_data(
                self.db_path,
                raw_data_id=raw_data_id,
                name=cleaned_data.get('name'),
                age=cleaned_data.get('age'),
                city=cleaned_data.get('city'),
                address=cleaned_data.get('address'),
                postal_code=cleaned_data.get('postal_code'),
                municipality=cleaned_data.get('municipality'),
                state_province=cleaned_data.get('state_province'),
                country=cleaned_data.get('country'),
                phone=cleaned_data.get('phone'),
                validation_notes=cleaned_data.get('validation_notes', ''),
                cleaned_by=cleaned_data.get('cleaned_by', 'claude-assistant')
            )

            # Log transformations
            for transformation in transformations:
                insert_audit_log(
                    self.db_path,
                    raw_data_id=raw_data_id,
                    cleaned_data_id=cleaned_id,
                    rule_applied=transformation['rule'],
                    description=transformation['description'],
                    applied_by='claude-assistant'
                )

            saved_count += 1

        return saved_count

    def pre_clean_batch(self, records: list) -> tuple[list, list]:
        """
        Apply deterministic cleaning to all records.
        Returns (all_pre_cleaned, needs_research) where needs_research is the
        subset that still require Claude for postal/municipality lookup.
        """
        from pre_cleaner import pre_clean_record, needs_research
        pre_cleaned = [pre_clean_record(r) for r in records]
        research_needed = [r for r in pre_cleaned if needs_research(r)]
        return pre_cleaned, research_needed

    def format_research_batch(self, records: list) -> str:
        """Format only the fields Claude needs for postal/municipality research."""
        if not records:
            return ""
        headers = ['ID', 'Name', 'Address', 'City', 'Postal Code', 'State/Prov', 'Country', 'Issue']
        rows = ['| ' + ' | '.join(headers) + ' |',
                '|' + '|'.join(['---'] * len(headers)) + '|']

        for r in records:
            postal = (r.get('postal_code') or '').strip()
            municipality = (r.get('municipality') or '').strip()
            import re
            postal_chars = re.sub(r'[\s\-]', '', postal)

            issues = []
            if not municipality or municipality.upper() == 'N/A':
                issues.append('municipality missing')
            if not postal_chars or len(postal_chars) < 5:
                issues.append('postal incomplete' if postal_chars else 'postal missing')

            row = [
                str(r['id']),
                r.get('name', ''),
                r.get('address', ''),
                r.get('city', ''),
                postal or 'N/A',
                r.get('state_province', ''),
                r.get('country', ''),
                '; '.join(issues),
            ]
            rows.append('| ' + ' | '.join(row) + ' |')

        return '\n'.join(rows)

    def parse_research_response(self, response: str) -> dict:
        """
        Parse Claude's focused 4-column response.
        Returns {record_id: {postal_code, municipality, validation_notes}}.
        """
        results = {}
        for line in response.strip().split('\n'):
            if not line.strip().startswith('|') or '---' in line:
                continue
            parts = [p.strip() for p in line.split('|')[1:-1]]
            if len(parts) < 3:
                continue
            try:
                record_id = int(parts[0])
            except ValueError:
                continue
            results[record_id] = {
                'postal_code': parts[1] if parts[1] not in ('N/A', '') else None,
                'municipality': parts[2] if parts[2] not in ('N/A', '') else None,
                'validation_notes': parts[3] if len(parts) > 3 else '',
            }
        return results

    def merge_results(self, pre_cleaned: list, research_results: dict) -> None:
        """
        Overlay Claude's research results onto pre-cleaned records
        and populate self.cleaned_results ready for save_cleaned_results().
        """
        self.cleaned_results = []
        for record in pre_cleaned:
            merged = dict(record)
            merged['raw_data_id'] = record['id']

            research = research_results.get(record['id'], {})
            if research.get('postal_code'):
                merged['postal_code'] = research['postal_code']
            if research.get('municipality'):
                merged['municipality'] = research['municipality']

            # Combine pre-clean change log with Claude's notes
            pre_changes = record.get('_pre_clean_changes', [])
            claude_notes = research.get('validation_notes', '')
            parts = []
            if pre_changes:
                parts.append('Pre-cleaned: ' + '; '.join(pre_changes))
            if claude_notes:
                parts.append(claude_notes)
            merged['validation_notes'] = ' | '.join(parts)
            merged.setdefault('cleaned_by', 'pre-cleaner+claude' if research else 'pre-cleaner')

            self.cleaned_results.append(merged)

    def generate_report(self) -> str:
        """Generate a summary report of cleaning results."""
        if not self.cleaned_results:
            return "No records were cleaned."

        report = f"\n{'='*80}\n"
        report += f"CLEANING REPORT\n"
        report += f"{'='*80}\n\n"

        report += f"Records Cleaned: {len(self.cleaned_results)}\n"
        report += f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        report += "Cleaned Records:\n"
        report += "-" * 80 + "\n"

        for record in self.cleaned_results:
            report += f"  ID {record['raw_data_id']}: {record['name']} ({record['country']})\n"

        report += f"\n{'='*80}\n"

        return report


# Utility functions for use in multi_turn_conversation
def initiate_cleaning_workflow(user_query: str) -> tuple:
    """Full workflow: interpret query -> fetch data -> format for Claude.

    Returns: (formatted_data_for_claude, agent_instance)
    """
    agent = DataCleaningAgent()

    # Step 1: Interpret user query
    filters = agent.interpret_user_query(user_query)

    # Step 2: Fetch matching data
    records = agent.fetch_data_for_query(filters)

    if not records:
        return "No records found matching your query.", agent

    # Step 3: Format for Claude
    formatted = agent.format_batch_for_claude(records)

    return formatted, agent


if __name__ == "__main__":
    # Example usage
    agent = DataCleaningAgent()

    # Example: User asks to clean Canadian data
    user_query = "clean all Canadian data"
    filters = agent.interpret_user_query(user_query)
    print(f"Interpreted filters: {filters}\n")

    records = agent.fetch_data_for_query(filters)
    print(f"Found {len(records)} records:\n")

    formatted = agent.format_batch_for_claude(records)
    print(formatted)
