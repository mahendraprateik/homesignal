import os
from dotenv import load_dotenv

load_dotenv()

print("Checking API keys...\n")

# Check Anthropic
anthropic_key = os.getenv('ANTHROPIC_API_KEY')
if not anthropic_key:
    print("FAIL: ANTHROPIC_API_KEY not found in .env")
elif not anthropic_key.startswith('sk-ant-'):
    print("WARN: ANTHROPIC_API_KEY found but format looks wrong")
else:
    print("PASS: ANTHROPIC_API_KEY found: sk-ant-..." + anthropic_key[-4:])

# Check FRED
fred_key = os.getenv('FRED_API_KEY')
if not fred_key:
    print("FAIL: FRED_API_KEY not found in .env")
elif len(fred_key) < 10:
    print("WARN: FRED_API_KEY found but seems too short")
else:
    print("PASS: FRED_API_KEY found: ..." + fred_key[-4:])

# Test Anthropic API call
print("\nTesting Anthropic API...")
try:
    import anthropic
    client = anthropic.Anthropic(api_key=anthropic_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": "Say hello"}]
    )
    print("PASS: Anthropic API works: " + message.content[0].text)
except Exception as e:
    print("FAIL: Anthropic API failed: " + str(e))

# Test FRED API call
print("\nTesting FRED API...")
try:
    from fredapi import Fred
    fred = Fred(api_key=fred_key)
    data = fred.get_series('MORTGAGE30US', limit=1)
    print("PASS: FRED API works: 30yr rate = " + str(round(data.iloc[-1], 2)) + "%")
except Exception as e:
    print("FAIL: FRED API failed: " + str(e))

print("\nDone!")
