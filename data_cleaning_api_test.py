import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

client = Anthropic(
    base_url="https://openrouter.ai/api",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

dataset = """
Name, Age, City, address, postalcode, Municipality
Alice, 30, Toronto, 25 Muir Ave.,  cookie, TBD
"""

# System prompt as separate variable - pass it each time
SYSTEM_PROMPT = """You are an expert at data cleaning for the real estate space.
You receive data in the following format: Name, Age, City, address, postalcode, Municipality
The first row is the header, and the rest are data rows. Clean the data based on these guidelines:

  1. The 'postalcode' column should follow Canadian postal code format (A1A 1A1).
  2. Try to infer postal codes from city and address, but use 'N/A' if unsure.
  3. You must validate the postalcode is for that address, do not guess.
  4. Only clean data you know about—don't guess or fill missing/invalid values. Use 'N/A' when uncertain.
  5. Fill 'municipality' with the accepted municipality for real estate in that city."""


# Call the Claude model
response = client.messages.create(
    model="anthropic/claude-haiku-4.5",
    max_tokens=2048,
    system=SYSTEM_PROMPT,
    messages=[
        {"role": "user",
         "content": """Clean this dataset:

Name, Age, City, address, postalcode, Municipality
Alice, 30, Toronto, 25 Muir Ave.,  cookie, TBD
"""
         }
    ]
)

print(response.content[0].text)
