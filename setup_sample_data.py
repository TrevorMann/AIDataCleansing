#!/usr/bin/env python3
import os
from database import init_db
from db_helpers import insert_raw_data
from config import DB_PATH

# Initialize database
print(f"Initializing database at {DB_PATH}...")
init_db(DB_PATH)

# Sample data across multiple countries with intentional errors
sample_records = [
    # CANADA (4 records - 1 with mismatched postal code)
    {
        "name": "john doe",
        "age": 35,
        "city": "toronto",
        "address": "123 Main St",
        "postal_code": "M5V1A1",
        "municipality": "N/A",
        "state_province": "ON",
        "country": "CA",
        "phone": "416-555-0123",
        "imported_by": "setup_script"
    },
    {
        "name": "Bob Builder",
        "age": 38,
        "city": "toronto",
        "address": "25 Muir Ave.",
        "postal_code": "M6H",
        "municipality": "N/A",
        "state_province": "ON",
        "country": "CA",
        "phone": "416755-0123",
        "imported_by": "setup_script"
    },
    {
        "name": "Fire Snow",
        "age": 33,
        "city": "Toronto",
        "address": "25 Muir Avenue",
        "postal_code": "M9L1H7",
        "municipality": "N/A",
        "state_province": "ON",
        "country": "CA",
        "phone": "14161230723",
        "imported_by": "setup_script"
    },
    {
        "name": "jane smith",
        "age": 28,
        "city": "vancouver",
        "address": "456 oak ave",
        "postal_code": "V6B1A1",
        "municipality": "N/A",
        "state_province": "BC",
        "country": "CA",
        "phone": "604-555-4567",
        "imported_by": "setup_script"
    },
    {
        "name": "robert johnson",
        "age": 42,
        "city": "montréal",
        "address": "789 Rue St-Laurent Blvd",
        "postal_code": "H1A1A1",
        "municipality": "N/A",
        "state_province": "QC",
        "country": "CA",
        "phone": "5145557890",
        "imported_by": "setup_script"
    },
    {
        "name": "elizabeth chen",
        "age": 36,
        "city": "toronto",
        "address": "321 Dundas West",
        "postal_code": "V6B1A1",
        "municipality": "N/A",
        "state_province": "ON",
        "country": "CA",
        "phone": "416-555-5555",
        "imported_by": "setup_script",
        "note": "MISMATCHED: Toronto address with Vancouver postal code"
    },
    # USA (3 records - 1 with mismatched postal code)
    {
        "name": "michael brown",
        "age": 38,
        "city": "new york",
        "address": "456 5th Avenue",
        "postal_code": "10001",
        "municipality": "N/A",
        "state_province": "NY",
        "country": "USA",
        "phone": "(212) 555-0198",
        "imported_by": "setup_script"
    },
    {
        "name": "sarah williams",
        "age": 31,
        "city": "los angeles",
        "address": "789 Sunset Blvd",
        "postal_code": "90210",
        "municipality": "N/A",
        "state_province": "CA",
        "country": "United States",
        "phone": "310-555-0147",
        "imported_by": "setup_script"
    },
    {
        "name": "david martinez",
        "age": 44,
        "city": "new york",
        "address": "555 Park Avenue",
        "postal_code": "90210",
        "municipality": "N/A",
        "state_province": "NY",
        "country": "USA",
        "phone": "2125550099",
        "imported_by": "setup_script",
        "note": "MISMATCHED: New York address with Los Angeles postal code"
    },
    # NETHERLANDS (3 records - 1 with mismatched postal code)
    {
        "name": "jan van der berg",
        "age": 45,
        "city": "Amsterdam",
        "address": "Prinsengracht 263",
        "postal_code": "1016HW",
        "municipality": "N/A",
        "state_province": "N/A",
        "country": "NL",
        "phone": "+31204261422",
        "imported_by": "setup_script"
    },
    {
        "name": "maria johansen",
        "age": 29,
        "city": "rotterdam",
        "address": "Witte de With Straat 12",
        "postal_code": "3012BJ",
        "municipality": "N/A",
        "state_province": "N/A",
        "country": "Netherlands",
        "phone": "0031102850000",
        "imported_by": "setup_script"
    },
    {
        "name": "peter de vries",
        "age": 51,
        "city": "amsterdam",
        "address": "Grimburgwal 7",
        "postal_code": "3012BJ",
        "municipality": "N/A",
        "state_province": "N/A",
        "country": "NL",
        "phone": "0031205551234",
        "imported_by": "setup_script",
        "note": "MISMATCHED: Amsterdam address with Rotterdam postal code"
    },
    # JAPAN (3 records - 1 with mismatched postal code)
    {
        "name": "tanaka hiroshi",
        "age": 52,
        "city": "Tokyo",
        "address": "2-1-1 Ginza, Chuo-ku",
        "postal_code": "104-0061",
        "municipality": "N/A",
        "state_province": "N/A",
        "country": "JP",
        "phone": "0312345678",
        "imported_by": "setup_script"
    },
    {
        "name": "suzuki akiko",
        "age": 27,
        "city": "osaka",
        "address": "1-2-3 Dotonbori, Chuo-ku",
        "postal_code": "542-0071",
        "municipality": "N/A",
        "state_province": "N/A",
        "country": "Japan",
        "phone": "+81-90-1234-5678",
        "imported_by": "setup_script"
    },
    {
        "name": "yamamoto yuki",
        "age": 39,
        "city": "tokyo",
        "address": "3-2-1 Shinjuku, Shinjuku-ku",
        "postal_code": "542-0071",
        "municipality": "N/A",
        "state_province": "N/A",
        "country": "Japan",
        "phone": "09012345678",
        "imported_by": "setup_script",
        "note": "MISMATCHED: Tokyo address with Osaka postal code"
    },
    # MEXICO (3 records - 1 with mismatched postal code)
    {
        "name": "carlos lopez garcia",
        "age": 41,
        "city": "mexico city",
        "address": "Paseo de la Reforma 505",
        "postal_code": "06500",
        "municipality": "N/A",
        "state_province": "CDMX",
        "country": "MX",
        "phone": "525555201234",
        "imported_by": "setup_script"
    },
    {
        "name": "rosa martinez ruiz",
        "age": 33,
        "city": "guadalajara",
        "address": "Av. Chapultepec 123",
        "postal_code": "44100",
        "municipality": "N/A",
        "state_province": "Jalisco",
        "country": "Mexico",
        "phone": "+52-33-3815-0123",
        "imported_by": "setup_script"
    },
    {
        "name": "luis hernandez",
        "age": 47,
        "city": "mexico city",
        "address": "Avenida Paseo 222",
        "postal_code": "44100",
        "municipality": "N/A",
        "state_province": "CDMX",
        "country": "Mexico",
        "phone": "5255558888",
        "imported_by": "setup_script",
        "note": "MISMATCHED: Mexico City address with Guadalajara postal code"
    },
]

# Insert sample records
print(f"\nInserting {len(sample_records)} sample records across 5 countries...\n")
countries_count = {}
for record in sample_records:
    country = record['country']
    countries_count[country] = countries_count.get(country, 0) + 1
    # Remove 'note' field if present (for documentation only)
    record_copy = {k: v for k, v in record.items() if k != 'note'}
    row_id = insert_raw_data(DB_PATH, **record_copy)
    # Print note if present
    note_str = f" ({record.get('note', '')})" if record.get('note') else ""
    print(f"  ✓ [{country}] {record['name']} from {record['city']} (ID: {row_id}){note_str}")

print(f"\n{'='*70}")
print(f"✅ Database initialized with {len(sample_records)} sample records!")
print(f"Database path: {DB_PATH}\n")
print("Records by country:")
for country, count in sorted(countries_count.items()):
    print(f"  - {country}: {count} records")
print(f"\n{'='*70}")
print(f"Next step: Run 'python multi_turn_conversation.py' to clean the data")
print(f"The data has intentional issues:")
print(f"  • Spelling errors and case inconsistencies")
print(f"  • Phone numbers in various formats needing standardization")
print(f"  • Postal codes requiring validation and formatting")
