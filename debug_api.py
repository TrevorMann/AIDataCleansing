import os
from dotenv import load_dotenv
from anthropic import Anthropic
import json

load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")

with open("debug_output.txt", "w") as f:
    f.write(f"API Key loaded: {api_key[:20]}...\n" if api_key else "NO API KEY FOUND\n")
    f.write(f"API Key format: {'Valid sk-or-v1' if api_key and api_key.startswith('sk-or-v1') else 'Invalid format'}\n\n")

    client = Anthropic(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    try:
        response = client.messages.create(
            model="anthropic/claude-haiku-4.5",
            max_tokens=100,
            messages=[
                {"role": "user", "content": "Say hello"}
            ]
        )
        f.write("Success!\n")
        f.write(response.content[0].text)
    except Exception as e:
        f.write(f"Error type: {type(e).__name__}\n")
        f.write(f"Error message: {str(e)}\n\n")
        f.write(f"Full error:\n{repr(e)}\n\n")
        if hasattr(e, 'response'):
            f.write(f"Response status: {e.response.status_code}\n")
            f.write(f"Response body:\n{e.response.text}\n")

print("Output written to debug_output.txt")
