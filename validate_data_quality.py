#!/usr/bin/env python3
import os
import sys
import re
from database import init_db
from db_helpers import get_all_raw_data
from config import DB_PATH

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

init_db(DB_PATH)

# Define validation rules
VALIDATION_RULES = {
    'case_issue': lambda v: v and v != v.strip() or (v and v[0].islower() if isinstance(v, str) else False),
    'postal_code_format': lambda v: v and (' ' not in v if '-' not in v else False),  # Missing space/dash
    'phone_format': lambda v: v and len(re.sub(r'[\s\-\(\)\+\.]', '', v)) < 8,  # Too short
    'country_abbrev': lambda c: c in ['CA', 'USA', 'MX', 'JP', 'NL'],  # Abbreviated
    'state_abbrev': lambda s: s and len(s) == 2 and s.isupper(),  # 2-letter abbreviation
    'unformatted_phone': lambda p: p and (p.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '').isdigit()),  # Raw digits
}

def check_record_quality(record):
    """Check a record for data quality issues."""
    issues = []

    # Check name case
    if record['name'] and record['name'][0].islower():
        issues.append('name_case')

    # Check city case
    if record['city'] and record['city'][0].islower():
        issues.append('city_case')

    # Check address case
    if record['address'] and record['address'][0].islower():
        issues.append('address_case')

    # Check postal code format (Canadian: A1A 1A1, US: 5 digits, others vary)
    postal = record['postal_code']
    if postal:
        if record['country'] in ['CA', 'Canada']:
            if not re.match(r'^[A-Z]\d[A-Z]\s?\d[A-Z]\d$', postal):
                issues.append('postal_format')
        elif record['country'] in ['USA', 'United States']:
            if not re.match(r'^\d{5}(-\d{4})?$', postal):
                issues.append('postal_format')
        elif record['country'] in ['MX', 'Mexico']:
            if not re.match(r'^\d{5}$', postal):
                issues.append('postal_format')

    # Check phone format
    phone = record['phone']
    if phone:
        # Check if it's unformatted (all digits/+)
        clean_phone = re.sub(r'[\s\-\(\)\+\.]', '', phone)
        if clean_phone.isdigit() or ('+' in phone and clean_phone[1:].isdigit()):
            issues.append('phone_unformatted')

    # Check country abbreviation
    if record['country'] in ['CA', 'USA', 'MX', 'JP', 'NL']:
        issues.append('country_abbrev')

    # Check state/province abbreviation
    state = record['state_province']
    if state and len(state) <= 2 and state.isupper() and state.isalpha():
        issues.append('state_abbrev')

    return issues

def get_records_needing_cleaning():
    """Get all records with identified issues."""
    all_records = get_all_raw_data(DB_PATH)
    records_with_issues = []

    for record in all_records:
        issues = check_record_quality(record)
        if issues:
            record['_issues'] = issues
            records_with_issues.append(record)

    return records_with_issues

def print_quality_report():
    """Print a data quality report."""
    all_records = get_all_raw_data(DB_PATH)
    records_with_issues = get_records_needing_cleaning()

    print(f"\n{'='*100}")
    print("DATA QUALITY ASSESSMENT REPORT")
    print(f"{'='*100}\n")

    print(f"Total Records: {len(all_records)}")
    print(f"Records Needing Cleaning: {len(records_with_issues)}")
    print(f"Records Already Clean: {len(all_records) - len(records_with_issues)}\n")

    if not records_with_issues:
        print("✅ All data is clean! No issues found.\n")
        return

    # Group by issue type
    issue_groups = {}
    for record in records_with_issues:
        for issue in record['_issues']:
            if issue not in issue_groups:
                issue_groups[issue] = []
            issue_groups[issue].append(record)

    print("Issues Found:")
    print("-" * 100)
    for issue, records in sorted(issue_groups.items()):
        print(f"\n  🔴 {issue}: {len(records)} records")
        for rec in records[:3]:  # Show first 3
            print(f"     - ID {rec['id']}: {rec['name']} ({rec['country']})")
        if len(records) > 3:
            print(f"     ... and {len(records) - 3} more")

    # Group by country
    print(f"\n\nRecords by Country (Needing Cleaning):")
    print("-" * 100)
    country_groups = {}
    for record in records_with_issues:
        country = record['country']
        if country not in country_groups:
            country_groups[country] = []
        country_groups[country].append(record)

    for country, records in sorted(country_groups.items()):
        print(f"  {country}: {len(records)} records")

    print(f"\n{'='*100}\n")

    return records_with_issues

if __name__ == "__main__":
    records = print_quality_report()
