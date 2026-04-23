import os
import re
import json
import time
import urllib.request
import urllib.parse
from anthropic import Anthropic
from database import init_db, get_db_connection
from db_helpers import insert_raw_data, get_all_raw_data, insert_cleaned_data, insert_audit_log
from schema_discovery import format_schema_for_prompt
from data_cleaning_agent import DataCleaningAgent

# Read .env file without requiring python-dotenv
def load_env():
    """Load environment variables from .env file."""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value

load_env()

# Initialize database
DB_PATH = os.getenv("DB_PATH", "data/cleaning.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
init_db(DB_PATH)
DB_SCHEMA = format_schema_for_prompt(DB_PATH)


def validate_na_phone(phone: str) -> bool:
    """Validate North American (US/Canada/Mexico) phone numbers.

    Accepts formats:
    - (123) 456-7890
    - 123-456-7890
    - 1231234567
    - +1-123-456-7890

    Returns True if valid, False otherwise.
    """
    if not phone or not isinstance(phone, str):
        return False

    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\.+]', '', phone)

    # Must be 10 digits (country code 1 is optional)
    if cleaned.startswith('1'):
        cleaned = cleaned[1:]

    # Check if exactly 10 digits and starts with valid area code
    if len(cleaned) != 10 or not cleaned.isdigit():
        return False

    # Area code cannot start with 0 or 1
    if cleaned[0] in ['0', '1']:
        return False

    return True


def validate_eu_phone(phone: str) -> bool:
    """Validate European phone numbers.

    Accepts formats:
    - +44 20 XXXX XXXX (UK)
    - +33 X XX XX XX XX (France)
    - +49 XXX XXXXXXX (Germany)
    - +39 XXX XXXXXX (Italy)
    - Etc. for other EU countries

    Returns True if valid, False otherwise.
    """
    if not phone or not isinstance(phone, str):
        return False

    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)\.]', '', phone)

    # Must start with + and country code
    if not cleaned.startswith('+'):
        return False

    # Remove the +
    cleaned = cleaned[1:]

    # Must have 2-3 digit country code + at least 6 more digits
    if len(cleaned) < 8 or not cleaned.isdigit():
        return False

    # Valid EU country codes are typically 1-3 digits
    country_code = cleaned[:3]
    if len(country_code) < 2:
        return False

    return True




def format_na_phone(phone: str) -> str:
    """Format North American phone number to (123) 456-7890 format.

    Returns formatted phone or 'N/A' if invalid.
    """
    if not validate_na_phone(phone):
        return 'N/A'

    # Clean the phone number
    cleaned = re.sub(r'[\s\-\(\)\.+]', '', phone)
    if cleaned.startswith('1'):
        cleaned = cleaned[1:]

    # Format as (123) 456-7890
    return f"({cleaned[:3]}) {cleaned[3:6]}-{cleaned[6:10]}"


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using Tavily API. Returns formatted results."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not set in environment."

    try:
        payload = json.dumps({
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        parts = []
        if data.get("answer"):
            parts.append(f"Summary: {data['answer']}\n")

        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            parts.append(
                f"{i}. {r.get('title', 'No title')}\n"
                f"   {r.get('content', '')[:300]}\n"
                f"   URL: {r.get('url', '')}"
            )

        return "\n".join(parts) if parts else f"No results found for: {query}"

    except Exception as e:
        return f"Web search failed: {e}. Query: {query}"


client = Anthropic(
    base_url="https://openrouter.ai/api",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

_BASE_SYSTEM_PROMPT = """You are an expert at data engineering your role is to clean and enhance data for the real estate space and personal information.
You receive data in the following format based on the database schema below:

{schema}

STEP 0 — CROSS-PROVINCE SANITY CHECK (run before any web search):
Canadian postal codes encode province in the first letter. Check this BEFORE anything else:
  A = Newfoundland & Labrador | B = Nova Scotia | C = Prince Edward Island
  E = New Brunswick | G/H/J = Quebec | K/L/M/N/P = Ontario
  R = Manitoba | S = Saskatchewan | T = Alberta | V = British Columbia
  X = NWT/Nunavut | Y = Yukon
If the postal code's first letter does NOT match the record's province/city, flag immediately:
  "CROSS-PROVINCE MISMATCH: [postal code] belongs to [actual province], record says [stated province/city]. REQUIRES MANUAL REVIEW."
Do NOT attempt to web search or correct — just flag and move on. This catches obvious data entry errors (e.g. V6B in a Toronto/Ontario record) without wasting a search.

POSTAL CODE CLASSIFICATION (determine type before any action):
There are three states — handle each differently:

  FULL postal code (6 characters, e.g. M6H 1E7, V6B 2W9):
  - NEVER modify or change. Treat as authoritative.
  - Web search to VERIFY it matches address + city.
  - If mismatch: flag as "POSTAL CODE MISMATCH: [code] does not match [address], [city]. KEEP ORIGINAL — requires review."
  - If confirmed: record confidence score in validation notes.

  PARTIAL postal code / FSA only (3 characters, e.g. M6H, V6B):
  - This is INCOMPLETE data, not a valid postal code. Must be resolved.
  - Web search "[street address] [FSA] [city]" to find the full postal code.
  - CRITICAL: The same street name can exist in multiple FSA areas (e.g. Muir Avenue exists
    under M6H AND M9L — these are different streets in different neighbourhoods). You MUST
    confirm which specific address matches before setting municipality or full code.
  - If web search returns exactly one confident match: complete the postal code and note
    "FSA [X] completed to [full code] via web search. Confidence: HIGH."
  - If ambiguous or multiple matches: set postal code to the FSA + "?" (e.g. "M6H ?"),
    set municipality to 'N/A', and flag "FSA AMBIGUOUS: multiple addresses found — requires manual review."
  - Never guess or assume the full code without a confirmed web result.

  Missing postal code:
  - Web search "[street address] [city] [province] postal code" to find it.
  - Only populate if search returns a single confident result.
  - If uncertain: leave as 'N/A' and note why.

MUNICIPALITY MAPPING (REAL ESTATE FOCUS):
Municipality MUST be filled in for EVERY record using the REAL ESTATE name — the neighbourhood
people actually search when looking for properties. This may differ from administrative boundaries
(e.g. "North York" not "Toronto", "Little Italy" not "Dufferin").

Process — follow in order:
1. Run cross-province check (Step 0) first. If flagged, set municipality to 'N/A' and skip remaining steps.
2. Determine postal code state (full / partial / missing) per rules above.
3. If full postal code: web search "[full postal code] real estate neighbourhood" to confirm municipality.
4. If partial FSA: web search "[address] [FSA] [city]" — resolve full postal code first, then derive municipality.
   - Remember: same address name in different FSA zones = different streets and different municipalities.
5. If missing postal code: web search "[address] [city] [province]" — resolve postal code first, then municipality.
6. Final cross-check: after cleaning, verify City + Address + Postal Code + Municipality all align.
   Record a confidence score (HIGH / MEDIUM / LOW) in validation notes.
   Flag any inconsistency even if you cannot resolve it.

When you cannot confidently determine a value:
- Postal Code: keep original (if full) or 'N/A' (if partial/missing after failed search)
- Municipality: 'N/A' with explanation — this should be rare
- City/Address: populate with best available information
- State/Province or Country: always use full names

CRITICAL: NEVER change a full postal code. Update surrounding fields to align with it, not the other way around.


The first row is the header, and the rest are data rows. Clean the data based on these guidelines:

<Phone Validation Rules>
NORTH AMERICAN (US/Canada/Mexico):
- Valid formats: (123) 456-7890, 123-456-7890, 1231234567, +1-123-456-7890
- Must be exactly 10 digits (country code 1 is optional)
- Area code cannot start with 0 or 1
- Standardize to: (123) 456-7890 format
- Example valid: (416) 555-0123 (Toronto), (514) 555-0123 (Montreal), (555) 123-4567 (US)

EUROPEAN:
- Valid formats: +44 20 XXXX XXXX, +33 X XX XX XX XX, +49 XXX XXXXXXX, etc.
- the + is optional and should be added when not present as long as rest of formatting is aligned
- Minimum 8 digits total including country code
- Standardize to: +[country code] [number] format
- Examples valid: +44 20 7123 4567 (UK), +33 1 23 45 67 89 (France), +49 30 12345678 (Germany)

VALIDATION RULES:
- If phone doesn't match NA or EU format, use 'N/A'
- Always validate format first
- Include country code in final output
- Use 'N/A' for any invalid phone numbers
</Phone Validation Rules>

GENERAL CLEANING RULES:
1. The 'postalcode' column should follow the standard of the country postal code format (A1A 1A1 for Canada, XXX XX for US, xxxx xx for Netherlands, etc.).
2. You must validate the postalcode is for that address, do not guess.
3. Use other data fields to fill in municipality, but use 'N/A' if unsure.
4. Standardize 'state/province' to be the full name (e.g. Ontario, not ON).
5. Standardize 'country' to be the full name (e.g. Canada, not CA or USA).
6. Validate and standardize phone numbers according to the rules above. Use 'N/A' if invalid.
7. Standardize street names in address (example: St. -> Street, Ave -> Avenue, Rd. -> Road, etc.).
8. YOU MUST FILL IN MUNICIPALITY if it is blank, or give reason why you did not fill in Municipality.  use websearch if you have to verify.
"""

SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT.format(schema=DB_SCHEMA)


class DataCleaningConversation:
    """Helper class for managing multi-turn conversations with hybrid approach."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.messages = []
        self.turn_count = 0

    def define_tools(self) -> list:
        """Define tools for Claude to call. Includes local validation functions + OpenRouter's server-side websearch."""
        return [
            {
                "name": "validate_na_phone",
                "description": "Validate if a phone number is North American (US/Canada/Mexico) format. Returns true/false.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": "The phone number to validate (e.g., '416-555-0123' or '(416) 555-0123')"
                        }
                    },
                    "required": ["phone"]
                }
            },
            {
                "name": "validate_eu_phone",
                "description": "Validate if a phone number is European format. Returns true/false.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": "The phone number to validate (e.g., '+44 20 7123 4567')"
                        }
                    },
                    "required": ["phone"]
                }
            },
            {
                "name": "format_na_phone",
                "description": "Format a North American phone number to (123) 456-7890 format. Returns formatted number or 'N/A' if invalid.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "description": "The phone number to format"
                        }
                    },
                    "required": ["phone"]
                }
            },
            {
                "name": "web_search",
                "description": "Search the web to verify addresses, postal codes, and municipalities. Use this to confirm that a postal code matches an address/city, find the real estate neighbourhood for a postal code or FSA, or resolve ambiguous addresses.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query, e.g. 'M6H 1E7 Toronto neighbourhood real estate', '25 Muir Avenue M6H Toronto postal code', 'V6B 2W9 Vancouver municipality'"
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results to return (default 5)",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        ]

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool function and return the result as a string."""
        if tool_name == "validate_na_phone":
            result = validate_na_phone(tool_input["phone"])
            return f"Valid North American phone: {result}"
        elif tool_name == "validate_eu_phone":
            result = validate_eu_phone(tool_input["phone"])
            return f"Valid European phone: {result}"
        elif tool_name == "format_na_phone":
            result = format_na_phone(tool_input["phone"])
            return f"Formatted NA phone: {result}"
        elif tool_name == "web_search":
            query = tool_input.get("query", "")
            max_results = tool_input.get("max_results", 5)
            result = web_search(query, max_results)
            print(f"     🔍 Search: {query}")
            print(f"     📄 Result preview: {result[:200]}...")
            return result
        else:
            return f"Unknown tool: {tool_name}"

    def preprocess_data(self, user_input: str) -> str:
        """Pre-process data before sending to Claude. Extract and clean what we can."""
        # This is where we'd extract phone numbers and validate them locally
        # For now, we'll keep it simple and just return the input
        # You could add regex to extract phones and pre-validate them
        return user_input

    def send_message(self, user_input: str) -> str:
        """
        Send a message with hybrid approach:
        1. Pre-process locally where possible
        2. Let Claude make decisions
        3. Claude can call tools if needed
        4. Handle tool calls and loop until done
        """
        # Step 1: Pre-process the input
        preprocessed = self.preprocess_data(user_input)

        # Step 2: Add user message
        self.messages.append({
            "role": "user",
            "content": preprocessed
        })

        # Step 3: Loop until Claude stops calling tools
        while True:
            response = client.messages.create(
                model="anthropic/claude-haiku-4.5",
                max_tokens=2048,
                system=self.system_prompt,
                messages=self.messages,
                tools=self.define_tools()
            )

            # Check if Claude called any tools
            tool_calls = [block for block in response.content if hasattr(block, 'type') and block.type == "tool_use"]

            # If no tool calls, Claude is done - return the response
            if not tool_calls:
                # Extract text response
                text_response = next(
                    (block.text for block in response.content if hasattr(block, 'text')),
                    "No response"
                )

                # Debug: Print full response content to see web search results
                print(f"\n📡 FULL API RESPONSE CONTENT:")
                print(f"{'-'*80}")
                for i, block in enumerate(response.content):
                    print(f"Block {i} (type: {block.type}):")
                    if hasattr(block, 'text'):
                        preview = block.text[:300] + "..." if len(block.text) > 300 else block.text
                        print(f"  Text: {preview}")
                    # Check for citations (web search results from OpenRouter)
                    if hasattr(block, 'citations') and block.citations:
                        print(f"  🔍 CITATIONS/WEB SEARCH RESULTS ({len(block.citations)} found):")
                        for j, citation in enumerate(block.citations, 1):
                            print(f"    Citation {j}: {citation}")
                print(f"{'-'*80}\n")

                self.messages.append({
                    "role": "assistant",
                    "content": response.content
                })
                return text_response

            # Step 4: Handle tool calls
            # Add Claude's response (which includes tool_use blocks)
            self.messages.append({
                "role": "assistant",
                "content": response.content
            })

            # Execute each tool and collect results
            tool_results = []
            for tool_use in tool_calls:
                tool_result = self.execute_tool(tool_use.name, tool_use.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": tool_result,
                    "status": "success"  # You could add error handling to set this to "error" if something goes wrong
                })
                print(f"  🔧 Tool called: {tool_use.name}")
                print(f"     Input: {tool_use.input}")
                print(f"     Result: {tool_result}")

            # Send tool results back to Claude
            self.messages.append({
                "role": "user",
                "content": tool_results
            })
            # Loop continues - Claude will process results and either call more tools or finish

    def display_message(self, role: str, content: str, turn: int = None):
        """Display a message nicely."""
        if turn:
            print(f"\n{'=' * 70}")
            print(f"TURN {turn}")
            print(f"{'=' * 70}")
        print(f"\n{role.upper()}:")
        print(content)

    def get_multiline_input(self, prompt: str = "Your message (type 'END' on a new line to finish):\n") -> str:
        """Get multi-line input from user."""
        print(f"\n{prompt}")
        lines = []
        while True:
            line = input()
            if line.strip().upper() == "END":
                break
            lines.append(line)
        return "\n".join(lines)

    def show_conversation_history(self):
        """Display the full conversation history."""
        print(f"\n{'=' * 70}")
        print("CONVERSATION HISTORY")
        print(f"{'=' * 70}")
        print(f"Total exchanges: {len(self.messages)}")
        print(f"Total turns: {self.turn_count}\n")
        for i, msg in enumerate(self.messages, 1):
            role = msg["role"].upper()
            content = msg["content"][:80] + "..." if len(msg["content"]) > 80 else msg["content"]
            print(f"{i}. {role}: {content}")

    def validate_phone(self, phone: str) -> dict:
        """Validate a phone number and return results."""
        na_valid = validate_na_phone(phone)
        eu_valid = validate_eu_phone(phone)

        result = {
            "phone": phone,
            "na_valid": na_valid,
            "eu_valid": eu_valid,
            "na_formatted": format_na_phone(phone) if na_valid else "N/A",
        }

        if na_valid:
            result["type"] = "North American"
            result["formatted"] = format_na_phone(phone)
        elif eu_valid:
            result["type"] = "European"
            result["formatted"] = phone  # Already in +XX format
        else:
            result["type"] = "Invalid"
            result["formatted"] = "N/A"

        return result

    def get_raw_data_for_cleaning(self) -> list:
        """Fetch uncleaned raw data from database."""
        return get_all_raw_data(DB_PATH)

    def save_cleaned_record(self, raw_data_id: int, cleaned_data: dict, validation_notes: str, user: str = "claude-assistant"):
        """Save cleaned data and create audit log entries."""
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
            validation_notes=validation_notes,
            cleaned_by=user
        )

        # Log transformations if needed
        if cleaned_data.get('transformations'):
            for transformation in cleaned_data['transformations']:
                insert_audit_log(
                    DB_PATH,
                    raw_data_id=raw_data_id,
                    cleaned_data_id=cleaned_id,
                    rule_applied=transformation.get('rule'),
                    description=transformation.get('description'),
                    applied_by=user
                )

        return cleaned_id

    def handle_cleaning_request(self, user_query: str) -> str:
        """Handle data cleaning workflow based on user query.

        1. Interpret query to understand what needs cleaning
        2. Fetch relevant data from database
        3. Present to Claude for cleaning
        4. Save cleaned results back to database
        """
        workflow_start = time.time()
        agent = DataCleaningAgent(DB_PATH)

        # Step 1: Interpret user query
        print(f"\n{'='*80}")
        print("STEP 1: INTERPRETING QUERY")
        print(f"{'='*80}")
        step1_start = time.time()
        filters = agent.interpret_user_query(user_query)
        step1_elapsed = time.time() - step1_start
        print(f"  User Query: '{user_query}'")
        print(f"  Interpreted Filters: {filters}")
        print(f"  ⏱️  STEP 1 TIME: {step1_elapsed:.2f}s\n")

        # Step 2: Fetch matching data
        print(f"{'='*80}")
        print("STEP 2: FETCHING DATA FROM DATABASE")
        print(f"{'='*80}")
        step2_start = time.time()
        records = agent.fetch_data_for_query(filters)
        step2_elapsed = time.time() - step2_start

        if not records:
            return "❌ No records found matching your query. Try: 'clean all Canadian data' or 'clean North American data'"

        print(f"  ✅ Found {len(records)} records matching query")
        print(f"  ⏱️  STEP 2 TIME: {step2_elapsed:.2f}s ({len(records)} records, {step2_elapsed/len(records):.3f}s per record)\n")

        # Step 3: Analyze data quality issues
        print(f"{'='*80}")
        print("STEP 3: DATA QUALITY ASSESSMENT")
        print(f"{'='*80}")
        step3_start = time.time()

        issue_summary = {}
        for record in records:
            for issue in record.get('_issues', []):
                if issue not in issue_summary:
                    issue_summary[issue] = 0
                issue_summary[issue] += 1

        step3_elapsed = time.time() - step3_start
        if issue_summary:
            for issue, count in sorted(issue_summary.items()):
                print(f"  🔴 {issue}: {count} records")
        print(f"  ⏱️  STEP 3 TIME: {step3_elapsed:.2f}s\n")

        # Step 4: Format for Claude
        print(f"{'='*80}")
        print("STEP 4: FORMATTING DATA FOR CLAUDE")
        print(f"{'='*80}")
        step4_start = time.time()
        formatted_data = agent.format_batch_for_claude(records)
        step4_elapsed = time.time() - step4_start
        print(f"  ✅ Formatted {len(records)} records as table")
        print(f"  ⏱️  STEP 4 TIME: {step4_elapsed:.2f}s\n")
        print(formatted_data)

        # Step 5: Ask Claude to clean this data
        cleaning_prompt = f"""Clean the following {len(records)} records per your system prompt rules.

EXECUTION ORDER — follow exactly, do not deviate:

PHASE 1 — BATCH ALL SEARCHES FIRST (do this before writing any output):
Scan every record and fire ALL web_search calls you will need in ONE batch:
- For each full postal code: one search to verify it matches the address/city
- For each partial FSA (3-char code): one search to resolve the full postal code and neighbourhood
- For each missing postal code: one search to find it
- For each cross-province mismatch you detect via the first-letter rule: no search needed, just flag it
Fire ALL searches now. Do not write the output table yet.

PHASE 2 — WRITE OUTPUT (only after all searches are complete):
Using the search results, produce the cleaned table. Apply all standardization rules:
- Names/cities: proper case
- Phone: NA format (123) 456-7890, EU format +XX XXX XXX
- Postal codes: A1A 1A1 (Canada), XXXXX (US), XXXX XX (Netherlands)
- State/Province and Country: full names
- Municipality: real estate neighbourhood name from search results

Return ONLY this table, no preamble:
| ID | Name | Age | City | Address | Postal Code | Municipality | State/Prov | Country | Phone | Validation Notes |

DATA TO CLEAN:
<raw_data>
{formatted_data}
</raw_data>
"""

        print(f"{'='*80}")
        print("STEP 5: SENDING TO CLAUDE FOR CLEANING")
        print(f"{'='*80}")
        print(f"  📤 Sending {len(records)} records to Claude...")
        print(f"  🤖 Model: anthropic/claude-haiku-4.5")
        print(f"  📊 Prompt size: {len(cleaning_prompt):,} characters\n")

        self.messages.append({
            "role": "user",
            "content": cleaning_prompt
        })

        step5_start = time.time()
        claude_response = "No response"
        search_count = 0
        tool_round = 0
        max_rounds = 3  # Phase 1 = searches, Phase 2 = output; cap prevents runaway loops

        while tool_round < max_rounds:
            tool_round += 1
            response = client.messages.create(
                model="anthropic/claude-haiku-4.5",
                max_tokens=4096,
                system=self.system_prompt,
                messages=self.messages,
                tools=self.define_tools()
            )

            tool_calls = [b for b in response.content if hasattr(b, 'type') and b.type == "tool_use"]

            # Print what came back this round
            print(f"\n  📡 Round {tool_round} ({len(tool_calls)} tool calls, stop={response.stop_reason}):")
            for block in response.content:
                if block.type == "tool_use":
                    tool_input = getattr(block, 'input', {})
                    if block.name == "web_search":
                        search_count += 1
                        print(f"    🔍 Search #{search_count}: {tool_input.get('query', '')}")
                    else:
                        print(f"    🔧 {block.name}: {tool_input}")
                elif block.type == "text" and hasattr(block, 'text') and block.text.strip():
                    preview = block.text[:200] + "..." if len(block.text) > 200 else block.text
                    print(f"    💬 {preview}")

            if not tool_calls:
                # No more tool calls — extract the final text response
                claude_response = next(
                    (b.text for b in response.content if hasattr(b, 'text')),
                    "No response"
                )
                self.messages.append({"role": "assistant", "content": response.content})
                break

            # Add assistant message (with tool_use blocks) then send back tool_results
            self.messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_use in tool_calls:
                result = self.execute_tool(tool_use.name, getattr(tool_use, 'input', {}))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result
                })

            self.messages.append({"role": "user", "content": tool_results})

        # If the loop exited via the cap (Claude still wanted more tools), force the final output
        if claude_response == "No response":
            print(f"\n  ⚠️  Round cap hit — forcing final output (no tools)...")
            final_response = client.messages.create(
                model="anthropic/claude-haiku-4.5",
                max_tokens=4096,
                system=self.system_prompt,
                messages=self.messages + [{
                    "role": "user",
                    "content": "You have completed your research. Now produce ONLY the cleaned data table — no preamble, no commentary."
                }]
            )
            claude_response = next(
                (b.text for b in final_response.content if hasattr(b, 'text')),
                "No response"
            )
            self.messages.append({"role": "assistant", "content": final_response.content})

        step5_elapsed = time.time() - step5_start
        print(f"\n  📈 Response size: {len(claude_response):,} characters | 🔍 Searches: {search_count} | Rounds: {tool_round}")
        print(f"  ⏱️  STEP 5 TIME (CLAUDE API): {step5_elapsed:.2f}s ({step5_elapsed/len(records):.3f}s per record)\n")

        print(f"{'='*80}")
        print("STEP 6: PARSING CLEANED DATA")
        print(f"{'='*80}")

        # Show Claude's raw response for debugging
        print(f"\n📋 Claude's Raw Response (first 800 chars):")
        print(f"{'-'*80}")
        print(claude_response[:800])
        print(f"{'-'*80}\n")

        # Step 6: Parse cleaned data
        step6_start = time.time()
        cleaned_records = agent.parse_cleaned_response(claude_response)
        step6_elapsed = time.time() - step6_start

        if not cleaned_records:
            print(f"  ❌ Parsing failed - no records extracted")
            print(f"\n⚠️ PARSING DEBUG INFO:")
            print(f"  - Response length: {len(claude_response)} characters")
            print(f"  - Contains pipes (|): {'|' in claude_response}")
            print(f"  - Contains 'Record': {'Record' in claude_response}")
            print(f"  - Contains 'ID': {'ID' in claude_response}")

            return f"""⚠️ Parser failed to extract data from Claude's response.

This usually means Claude's response format doesn't match the expected table format.

SOLUTION OPTIONS:
1. Check the response above - does it have a proper table with pipes (|)?
2. Ask Claude to return data in a clean table format with:
   | ID | Name | Age | City | Address | Postal Code | State/Prov | Country | Phone |

3. Run again - sometimes Claude returns slightly different formats

CLAUDE'S RESPONSE (for manual review):
{claude_response}"""

        # Validate parsed records
        print(f"  ✅ Successfully parsed {len(cleaned_records)} cleaned records")
        print(f"  ⏱️  STEP 6 TIME: {step6_elapsed:.2f}s ({step6_elapsed/len(cleaned_records) if cleaned_records else 0:.3f}s per record)")

        # Check for null values
        null_count = 0
        for record in cleaned_records:
            for key, value in record.items():
                if value is None or value == '' or value == 'N/A':
                    null_count += 1

        if null_count > len(cleaned_records) * 3:  # More than 3 nulls per record
            print(f"\n  ⚠️  WARNING: {null_count} null/empty values found in parsed records")
            print(f"  This might indicate a parsing issue. Review the data above.\n")
        else:
            print()

        # Step 7: Save results with detailed logging
        print(f"{'='*80}")
        print("STEP 7: SAVING TO DATABASE")
        print(f"{'='*80}")

        step7_start = time.time()
        for i, cleaned_data in enumerate(agent.cleaned_results, 1):
            raw_data_id = cleaned_data['raw_data_id']
            original = next((r for r in records if r['id'] == raw_data_id), None)

            print(f"\n  Record {i}/{len(agent.cleaned_results)} (ID {raw_data_id}):")
            print(f"    Name: {original['name']} → {cleaned_data['name']}")
            print(f"    Phone: {original['phone']} → {cleaned_data['phone']}")
            print(f"    Postal: {original['postal_code']} → {cleaned_data['postal_code']}")
            print(f"    State/Prov: {original['state_province']} → {cleaned_data['state_province']}")
            print(f"    Country: {original['country']} → {cleaned_data['country']}")

        saved_count = agent.save_cleaned_results()
        step7_elapsed = time.time() - step7_start
        print(f"\n  ✅ Saved {saved_count} cleaned records to database")
        print(f"  ⏱️  STEP 7 TIME: {step7_elapsed:.2f}s ({step7_elapsed/saved_count if saved_count else 0:.3f}s per record)\n")

        # Step 8: Generate report
        print(f"{'='*80}")
        print("STEP 8: COMPLETION REPORT")
        print(f"{'='*80}")
        step8_start = time.time()
        report = agent.generate_report()
        step8_elapsed = time.time() - step8_start
        report += f"\n✅ Cleaning workflow complete! {saved_count} records saved.\n"

        # Print overall timing summary
        total_elapsed = time.time() - workflow_start
        print(f"\n{'='*80}")
        print("⏱️  TIMING SUMMARY")
        print(f"{'='*80}")
        print(f"  Step 1 (Interpret Query):    {step1_elapsed:7.2f}s ({step1_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  Step 2 (Fetch Data):         {step2_elapsed:7.2f}s ({step2_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  Step 3 (Quality Assessment): {step3_elapsed:7.2f}s ({step3_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  Step 4 (Format Data):        {step4_elapsed:7.2f}s ({step4_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  Step 5 (Claude API):         {step5_elapsed:7.2f}s ({step5_elapsed/total_elapsed*100:5.1f}%) ⭐ BOTTLENECK")
        print(f"  Step 6 (Parse Results):      {step6_elapsed:7.2f}s ({step6_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  Step 7 (Save to DB):         {step7_elapsed:7.2f}s ({step7_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  Step 8 (Report):             {step8_elapsed:7.2f}s ({step8_elapsed/total_elapsed*100:5.1f}%)")
        print(f"  {'-'*50}")
        print(f"  TOTAL:                       {total_elapsed:7.2f}s")
        print(f"  Average per record:          {total_elapsed/len(records):7.3f}s")
        print(f"  Estimated time for 100k:     {(total_elapsed/len(records))*100000/3600:7.1f}h ({(total_elapsed/len(records))*100000/60:7.0f}m)")
        print(f"{'='*80}\n")

        return report

    def run_interactive(self):
        """Run interactive conversation mode with hybrid approach."""
        print(f"{'=' * 70}")
        print("DATA CLEANING CONVERSATION (HYBRID: Pre-Process + Tool Use + DB Integration)")
        print(f"{'=' * 70}")
        print("System: Data cleaning expert for real estate")
        print("\nWorkflow:")
        print("  1️⃣  Ask a question or start a cleaning workflow")
        print("  2️⃣  Claude validates & cleans data")
        print("  3️⃣  Results automatically saved to database\n")
        print("Commands:")
        print("  • CLEAN [query] - Start automated cleaning workflow")
        print("    Examples: 'CLEAN Canadian data', 'CLEAN North American data'")
        print("  • HISTORY - View conversation history")
        print("  • QUIT - Exit\n")

        while True:
            self.turn_count += 1
            user_input = self.get_multiline_input(
                f"Turn {self.turn_count} - Your message (type 'END' on new line to submit):"
            )

            if user_input.strip().upper() == "QUIT":
                print("\nGoodbye!")
                break
            elif user_input.strip().upper() == "HISTORY":
                self.show_conversation_history()
                self.turn_count -= 1
                continue
            elif user_input.strip().upper().startswith("CLEAN"):
                # Extract the cleaning query (e.g., "CLEAN Canadian data" -> "Canadian data")
                clean_query = user_input[5:].strip()
                if not clean_query:
                    print("\n📋 Available cleaning queries:")
                    print("  CLEAN Canadian data")
                    print("  CLEAN US data")
                    print("  CLEAN Mexican data")
                    print("  CLEAN North American data")
                    print("  CLEAN all uncleaned data")
                    self.turn_count -= 1
                    continue

                print(f"\n{'='*70}")
                print(f"CLEANING WORKFLOW: {clean_query}")
                print(f"{'='*70}")

                result = self.handle_cleaning_request(clean_query)
                print(result)
                continue
            elif user_input.strip().upper() == "LOAD_FROM_DB":
                raw_records = self.get_raw_data_for_cleaning()
                if raw_records:
                    print(f"\nFound {len(raw_records)} raw records in database:")
                    for i, record in enumerate(raw_records, 1):
                        print(f"  {i}. ID {record['id']}: {record['name']} ({record['city']})")
                else:
                    print("\nNo raw data in database. Use INSERT command to add data.")
                self.turn_count -= 1
                continue
            elif not user_input.strip():
                print("Please enter a message.")
                self.turn_count -= 1
                continue

            print(f"\n[Processing with hybrid approach...]")
            response = self.send_message(user_input)
            self.display_message("assistant", response)


def test_phone_validation():
    """Test the phone validation functions."""
    print(f"\n{'=' * 70}")
    print("PHONE VALIDATION TEST")
    print(f"{'=' * 70}\n")

    test_numbers = [
        # North American
        "(416) 555-0123",
        "416-555-0123",
        "4165550123",
        "+1-416-555-0123",
        # European
        "+44 20 7123 4567",
        "+33 1 23 45 67 89",
        "+49 30 12345678",
        # Invalid
        "123456",
        "invalid",
        "555-1234",
    ]

    conversation = DataCleaningConversation(system_prompt=SYSTEM_PROMPT)

    for phone in test_numbers:
        result = conversation.validate_phone(phone)
        print(f"Phone: {result['phone']}")
        print(f"  Type: {result['type']}")
        print(f"  Formatted: {result['formatted']}")
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_phone_validation()
    else:
        conversation = DataCleaningConversation(system_prompt=SYSTEM_PROMPT)
        conversation.run_interactive()
