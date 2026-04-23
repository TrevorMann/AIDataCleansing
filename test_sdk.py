import os
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

# Try different base URL formats
urls_to_try = [
    "https://openrouter.ai/api/v1",
    "https://openrouter.ai/api",
    "https://openrouter.ai",
]

api_key = os.getenv("OPENROUTER_API_KEY")

for base_url in urls_to_try:
    print(f"\nTrying base_url: {base_url}")
    try:
        client = Anthropic(
            base_url=base_url,
            api_key=api_key,
        )

        response = client.messages.create(
            model="anthropic/claude-haiku-4.5",
            max_tokens=100,
            messages=[
                {"role": "user", "content": "Say hello"}
            ]
        )
        print("✓ SUCCESS!")
        print(f"Response: {response.content[0].text[:100]}")
        break
    except Exception as e:
        print(f"✗ Error: {type(e).__name__}")
        if hasattr(e, 'status_code'):
            print(f"  Status: {e.status_code}")
