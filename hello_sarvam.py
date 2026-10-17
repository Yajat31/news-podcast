import os
from pathlib import Path

from sarvamai import SarvamAI

for line in Path(__file__).with_name(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
resp = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[{"role": "user", "content": "Say hello in Hindi and English, one line each."}],
)
print(resp.choices[0].message.content)
