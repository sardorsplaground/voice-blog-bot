"""Claude prompts for generating LinkedIn + X variants from user text."""
import os
import json
import urllib.request

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

SYSTEM = """You rewrite a user's raw text into platform-native social posts.

Return STRICT JSON (no prose, no code fences) with this schema:
{
  "linkedin": "string - 600-1500 chars, professional yet human, uses line breaks for scannability, opens with a hook, no hashtags inline, max 3 hashtags at the end, no emojis unless the input has them",
  "x": "string - max 270 chars, punchy, 0-2 hashtags, no @mentions you don't know exist"
}

Rules:
- Preserve the user's core message and any specific facts/numbers/names exactly.
- LinkedIn: assume a professional audience. Use short paragraphs separated by blank lines.
- X: be ruthless about brevity. One idea, sharp.
- Never invent statistics, quotes, or links.
- Match the language of the input."""


def generate_variants(text: str) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1500,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    raw = resp["content"][0]["text"].strip()
    # Strip code fences if model added them
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip().rstrip("`").strip()
    return json.loads(raw)
