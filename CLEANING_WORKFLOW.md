# Data Cleaning Workflow - Complete Integration

## Overview

The data cleaning system is now fully integrated into `multi_turn_conversation.py` with automatic database integration. No manual CSV extraction or copying needed.

## Architecture

```
User Input
    ↓
multi_turn_conversation.py (CLEAN command)
    ↓
DataCleaningAgent.interpret_user_query()
    ↓
database queries for matching records
    ↓
validate_data_quality.py (pre-checks)
    ↓
Format for Claude
    ↓
Claude cleans data
    ↓
Parse cleaned response
    ↓
Save to cleaned_data table
    ↓
Log transformations in audit_log
    ↓
Report results
```

## How to Use

### 1. Start the Interactive Session

```bash
python multi_turn_conversation.py
```

### 2. Initiate Cleaning Workflow

Type `CLEAN` followed by what you want to clean:

```
CLEAN Canadian data
CLEAN US data
CLEAN Mexican data
CLEAN North American data
CLEAN all uncleaned data
```

### 3. Workflow Executes Automatically

**Step 1: Query Interpretation**
- Agent parses your request
- Identifies country filters, issue types, scope
- Example: "CLEAN Canadian data" → country: CA

**Step 2: Data Validation**
- Fetches raw records from `raw_data` table
- Runs pre-checks to identify issues:
  - Case problems (lowercase names)
  - Phone format issues
  - Postal code format issues
  - Country/state abbreviations
- Only problematic records are queued

**Step 3: Presentation to Claude**
- Formats data as clean table with issue annotations
- Includes issue types (name_case, phone_unformatted, etc.)
- Shows only the fields that need cleaning

**Step 4: Claude Cleans**
- Uses system prompt rules for standardization
- Returns cleaned data in same table format
- Identifies data quality warnings

**Step 5: Results Saved Automatically**
- Parses Claude's response back into structured data
- Detects what changed (transformations)
- Saves to `cleaned_data` table
- Logs each transformation in `audit_log` table
- Reports success

## Example Session

```
$ python multi_turn_conversation.py

================================================================================
DATA CLEANING CONVERSATION (HYBRID: Pre-Process + Tool Use + DB Integration)
================================================================================
System: Data cleaning expert for real estate

Workflow:
  1️⃣  Ask a question or start a cleaning workflow
  2️⃣  Claude validates & cleans data
  3️⃣  Results automatically saved to database

Commands:
  • CLEAN [query] - Start automated cleaning workflow
    Examples: 'CLEAN Canadian data', 'CLEAN North American data'
  • HISTORY - View conversation history
  • QUIT - Exit

Turn 1 - Your message (type 'END' on new line to submit):
CLEAN Canadian data
END

======================================================================
CLEANING WORKFLOW: Canadian data
======================================================================

🔍 Query interpreted: {'country': 'CA', 'scope': 'all_uncleaned'}

📊 Found 7 records to clean:

ID   | Name               | Age   | City         | Address                   | Postal Code  | State/Prov | Country      | Phone            | Issues
...

🤖 Sending to Claude for cleaning...

================================================================================
CLEANING REPORT
================================================================================

Records Cleaned: 7
Timestamp: 2024-04-21 14:32:15

Cleaned Records:
  ID 4: John Doe (Canada)
  ID 5: Jane Smith (Canada)
  ... [more records]

✅ Saved 7 cleaned records to database.
```

## Supported Cleaning Queries

### By Country
- `CLEAN Canadian data` → CA records
- `CLEAN US data` → USA records
- `CLEAN American data` → USA records
- `CLEAN Mexican data` → Mexico records
- `CLEAN Dutch data` → Netherlands records
- `CLEAN Japanese data` → Japan records

### By Region
- `CLEAN North American data` → CA, USA, Mexico

### By Issue Type
- `CLEAN records with bad phone numbers` → phone_unformatted issues
- `CLEAN postal code issues` → postal_format issues
- `CLEAN case issues` → name_case, city_case issues

### All Data
- `CLEAN all uncleaned data` → All records needing cleaning
- `CLEAN first batch` → Limit to first 5 records

## Database Tables Used

### raw_data
- Original imported data
- Contains uncleaned records

### cleaned_data
- Cleaned records
- Linked to raw_data by raw_data_id
- Includes validation_notes

### audit_log
- Tracks each transformation
- rule_applied: what was changed (name_case, phone_format, etc.)
- description: before → after
- Links to both raw_data_id and cleaned_data_id

## Validation Rules Applied by Claude

Based on `multi_turn_conversation.py` system prompt:

**Phone Numbers:**
- North American: (123) 456-7890 format
- European: +XX format
- Invalid: flagged as 'N/A'

**Postal Codes:**
- Canada: A1A 1A1 format
- USA: XXXXX or XXXXX-XXXX
- Mexico: XXXXX
- Netherlands: XXXX XX format
- Must match address city (validated via web search)

**Names & Locations:**
- Proper capitalization (John Doe, not john doe)
- Full country names (Canada, not CA)
- Full province/state names (Ontario, not ON)

**Address Standards:**
- Street abbreviations expanded (St → Street, Ave → Avenue)
- Postal code spacing normalized

## Token Efficiency

The workflow is designed to be token-efficient:

1. **Pre-validation (free)** - validates before sending to Claude
2. **Batch processing** - cleans by country to reduce context
3. **Selective sending** - only problem records go to Claude
4. **Auto-saving** - no manual copy/paste cycles
5. **Issue tracking** - shows what each record needed

## Troubleshooting

**"No records found matching your query"**
- All data might already be cleaned
- Run `validate_data_quality.py` to check status
- Try `CLEAN all uncleaned data` for a broader search

**"Could not parse cleaned data from response"**
- Claude's response format might be different
- Try again or check Claude's response for formatting issues
- System prompt may need adjustment

**Cleaned data not saving**
- Check database path in `.env`
- Verify database file exists
- Check file permissions on database directory

## Files

- `multi_turn_conversation.py` - Main interactive session with integrated cleaning
- `data_cleaning_agent.py` - Agent that orchestrates the workflow
- `validate_data_quality.py` - Pre-checks for data quality issues
- `database.py` - Database schema and connection
- `db_helpers.py` - CRUD operations
- `schema_discovery.py` - Dynamic schema introspection
