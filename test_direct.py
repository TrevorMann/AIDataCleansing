import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")

# Test the endpoint directly with requests
url = "https://openrouter.ai/api/v1/messages"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}
data = {
    "model": "anthropic/claude-haiku-4.5",
    "max_tokens": 100,
    "messages": [
        {"role": "user", "content": "Say hello"}
    ]
}

print(f"URL: {url}")
print(f"API Key: {api_key[:20]}...")
print()

response = requests.post(url, json=data, headers=headers)
print(f"Status Code: {response.status_code}")
print(f"Response Type: {response.headers.get('content-type')}")
print()
print("Response (first 500 chars):")
print(response.text[:500])
