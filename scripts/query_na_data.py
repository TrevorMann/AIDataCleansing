#!/usr/bin/env python3
import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_helpers import get_all_raw_data

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

# Ensure directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Import and initialize database
from database import init_db
init_db(DB_PATH)

# Fetch all raw data
all_records = get_all_raw_data(DB_PATH)

# Filter for North American records (CA, USA, USA, Mexico, MX, United States)
na_countries = ['CA', 'USA', 'United States', 'MX', 'Mexico']
na_records = [r for r in all_records if r['country'] in na_countries]

print(f"\n{'='*120}")
print(f"NORTH AMERICAN DATA ({len(na_records)} records)")
print(f"{'='*120}\n")

# Display as table
if na_records:
    # Headers
    headers = ['ID', 'Name', 'Age', 'City', 'Address', 'Postal Code', 'State/Prov', 'Country', 'Phone']
    col_widths = [4, 20, 5, 15, 30, 12, 10, 15, 18]

    header_row = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(header_row)
    print("-" * 120)

    for record in na_records:
        row = [
            str(record['id']),
            record['name'][:20],
            str(record['age']) if record['age'] else 'N/A',
            record['city'][:15],
            record['address'][:30],
            record['postal_code'][:12],
            record['state_province'][:10],
            record['country'][:15],
            record['phone'][:18]
        ]
        row_str = " | ".join(str(v).ljust(w) for v, w in zip(row, col_widths))
        print(row_str)

    print(f"\n{'='*120}")
    print(f"Total NA Records: {len(na_records)}")
    print(f"Countries: {', '.join(set(r['country'] for r in na_records))}")
    print(f"{'='*120}\n")

    # Print raw data as CSV for easy copying to Claude
    print("\nRAW DATA (CSV Format for Claude):\n")
    print(",".join(headers))
    for record in na_records:
        row = [
            str(record['id']),
            record['name'],
            str(record['age']) if record['age'] else '',
            record['city'],
            record['address'],
            record['postal_code'],
            record['state_province'],
            record['country'],
            record['phone']
        ]
        print(",".join(row))
else:
    print("No North American records found in database.\n")
