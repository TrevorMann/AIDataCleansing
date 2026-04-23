import os
import re
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()


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


fsa_mapping = [
    {
        "FSA": "M5V",
        "Municipality": "Toronto",
    },
    {
        "FSA": "M4B",
        "Municipality": "Toronto",
    },
    {
        "FSA": "M4C",
        "Municipality": "Toronto",
    },
    {
        "FSA": "M9L",
        "Municipality": "North York",
    }
]

client = Anthropic(
    base_url="https://openrouter.ai/api",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

SYSTEM_PROMPT = f"""You are an expert at data cleaning for the real estate space and personal information.
You receive data in the following format: Name, Age, City, address, postalcode, Municipality, postal code, state/province, country, phone

Use Websearch to help validate information, like looking up FSA mapping data to determine municipality based on postal code.
Use web search if you have full postal code to standardize address.

For Dutch addresses (Netherlands):
  - Search for the street name + house number + city to verify the correct postal code
  - If the postal code doesn't match web results, flag as 'not valid'                                                                                                                                          
  - Example: "Van Hoytemastraat 2596 ES" is correct; "van Hoystema Strat and 2596 XA" is invalid because:
    1. Street name is misspelled/abbreviated
    2. Postal code XA doesn't match the real address

The first row is the header, and the rest are data rows. Clean the data based on these guidelines:

<FSA Mapping>
{fsa_mapping}
</FSA Mapping>

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
3. Use FSA mapping to fill in municipality, but use 'N/A' if unsure.
4. Fill 'municipality' with the accepted municipality for real estate in that city.
5. Standardize 'state/province' to be the full name (e.g. Ontario, not ON).
6. Standardize 'country' to be the full name (e.g. Canada, not CA or USA).
7. Validate and standardize phone numbers according to the rules above. Use 'N/A' if invalid.
8. Standardize street names in address (example: St. -> Street, Ave -> Avenue, Rd. -> Road, etc.).
"""


class DataCleaningConversation:
    """Helper class for managing multi-turn conversations with hybrid approach."""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.messages = []
        self.turn_count = 0

    def define_tools(self) -> list:
        """Define tools for Claude to call. These are the validation functions."""
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
                tools=self.define_tools()  # Make tools available to Claude
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
                    "content": tool_result
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

    def run_interactive(self):
        """Run interactive conversation mode with hybrid approach."""
        print(f"{'=' * 70}")
        print("DATA CLEANING CONVERSATION (HYBRID: Pre-Process + Tool Use)")
        print(f"{'=' * 70}")
        print("System: Data cleaning expert for real estate")
        print("\nWorkflow:")
        print("  1️⃣  Your input → Pre-processed locally")
        print("  2️⃣  Claude analyzes → Can call validation tools")
        print("  3️⃣  Tools execute → Results sent back to Claude")
        print("  4️⃣  Claude finalizes answer\n")
        print("Commands: 'QUIT' to exit, 'HISTORY' to see full conversation\n")

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
