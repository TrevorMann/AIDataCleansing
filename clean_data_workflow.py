#!/usr/bin/env python3
import os
import sys
from database import init_db
from db_helpers import get_all_raw_data, insert_cleaned_data, insert_audit_log
from validate_data_quality import get_records_needing_cleaning

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Read DB_PATH from .env
def load_db_path():
    """Load DB_PATH from .env file without requiring python-dotenv."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    db_path = "F:\\sqlliteDB\\datacleansingDB.sqlite"  # Default fallback

    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                if line.startswith('DB_PATH='):
                    db_path = line.split('=', 1)[1].strip()
                    break

    return db_path

DB_PATH = load_db_path()
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
init_db(DB_PATH)

def get_records_by_country(country_filter=None):
    """Get records needing cleaning, optionally filtered by country."""
    records = get_records_needing_cleaning()

    if country_filter:
        records = [r for r in records if r['country'].upper() in [country_filter.upper(), country_filter[:2].upper()]]

    return records

def format_records_for_claude(records):
    """Format records as a table string for Claude."""
    if not records:
        return "No records to clean."

    headers = ['ID', 'Name', 'Age', 'City', 'Address', 'Postal Code', 'State/Prov', 'Country', 'Phone']
    col_widths = [4, 20, 5, 15, 30, 12, 10, 12, 18]

    table = ""
    header_row = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    table += header_row + "\n"
    table += "-" * 120 + "\n"

    for record in records:
        row = [
            str(record['id']),
            record['name'][:20],
            str(record['age']) if record['age'] else 'N/A',
            record['city'][:15],
            record['address'][:30],
            record['postal_code'][:12],
            record['state_province'][:10],
            record['country'][:12],
            record['phone'][:18]
        ]
        row_str = " | ".join(str(v).ljust(w) for v, w in zip(row, col_widths))
        table += row_str + "\n"

    return table

def show_cleaning_menu():
    """Show menu for what to clean."""
    records_needing = get_records_needing_cleaning()

    if not records_needing:
        print("\n✅ All data is clean! No cleaning needed.\n")
        return None

    print(f"\n{'='*100}")
    print("DATA CLEANING WORKFLOW")
    print(f"{'='*100}\n")

    print(f"Found {len(records_needing)} records needing cleaning.\n")

    # Group by country
    country_groups = {}
    for record in records_needing:
        country = record['country']
        if country not in country_groups:
            country_groups[country] = []
        country_groups[country].append(record)

    print("Available batches to clean:")
    countries = sorted(country_groups.keys())
    for i, country in enumerate(countries, 1):
        print(f"  {i}. {country}: {len(country_groups[country])} records")

    print(f"  {len(countries) + 1}. ALL: {len(records_needing)} records across all countries")
    print(f"  0. Exit\n")

    choice = input("Enter choice (0-{0}): ".format(len(countries) + 1)).strip()

    if choice == "0":
        return None

    try:
        idx = int(choice) - 1
        if idx == len(countries):  # ALL option
            return records_needing
        elif 0 <= idx < len(countries):
            return country_groups[countries[idx]]
        else:
            print("Invalid choice.")
            return None
    except ValueError:
        print("Invalid input.")
        return None

def save_cleaned_batch(cleaned_data_list):
    """Save batch of cleaned records to database."""
    for cleaned_data in cleaned_data_list:
        raw_data_id = cleaned_data['raw_data_id']

        cleaned_id = insert_cleaned_data(
            DB_PATH,
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
        if cleaned_data.get('transformations'):
            for transformation in cleaned_data['transformations']:
                insert_audit_log(
                    DB_PATH,
                    raw_data_id=raw_data_id,
                    cleaned_data_id=cleaned_id,
                    rule_applied=transformation.get('rule'),
                    description=transformation.get('description'),
                    applied_by='claude-assistant'
                )

    return len(cleaned_data_list)

def show_batch_for_cleaning(records):
    """Display batch of records that need cleaning."""
    print(f"\n{'='*100}")
    print(f"BATCH: {len(records)} records to clean")
    print(f"{'='*100}\n")

    table = format_records_for_claude(records)
    print(table)

    print("\n📋 Copy this table and paste into Claude with your data cleaning prompt.")
    print("🤖 Claude will clean the data according to your system prompt rules.")
    print("\n" + "="*100 + "\n")

if __name__ == "__main__":
    batch = show_cleaning_menu()

    if batch:
        show_batch_for_cleaning(batch)
        print(f"\nAfter Claude cleans the data, you can save results with: save_cleaned_batch(cleaned_data)")
