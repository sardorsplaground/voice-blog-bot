"""Claude prompts for generating LinkedIn + X + Telegram + Blog variants from user text."""
import os
import json
import urllib.request

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

SYSTEM = """You rewrite a user's raw text into platform-native posts for four channels.

Return STRICT JSON (no prose, no code fences) with this schema:
{
  "linkedin": "string - 600-1500 chars, professional yet human, line breaks for scannability, hook opener, max 3 hashtags at end",
  "x": "string - max 270 chars, punchy, 0-2 hashtags",
  "tg": "string - up to 1500 chars, conversational Telegram channel post",
  "blog": {
    "title": "string - 40-80 char engaging blog title, no clickbait",
    "content": "string - 800-2000 char Markdown blog post with H2/H3 headings, short paragraphs, and a closing takeaway. Do NOT repeat the title inside content.",
    "tags": ["string", "string", "string"]  // 2-5 lowercase topic tags
  }
}

Rules:
- Preserve the user's core message and any specific facts/numbers/names exactly.
- Never invent statistics, quotes, or links.
- Match the language of the input.
- The blog variant expands on the idea with context, structure, and practical insight — it is not just a longer social post."""


def _fallback_title(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "Untitled post"
    first_line = text.split("\n", 1)[0].strip()
    if 10 <= len(first_line) <= 80:
        return first_line
    return (first_line[:77].rstrip() + "…") if len(first_line) > 80 else (first_line or "Untitled post")


def format_variants(text: str) -> dict:
    """Default: no AI. Just produce per-platform versions trimmed to platform limits."""
    text = (text or "").strip()
    li = text if len(text) <= 3000 else text[:2997].rstrip() + "…"
    x = text if len(text) <= 280 else text[:277].rstrip() + "…"
    tg = text if len(text) <= 4000 else text[:3997].rstrip() + "…"
    blog = {
        "title": _fallback_title(text),
        "content": text,
        "tags": [],
    }
    return {"linkedin": li, "x": x, "tg": tg, "blog": blog}


def generate_variants(text: str) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 2500,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": text}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    raw = resp["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip().rstrip("`").strip()
    parsed = json.loads(raw)
    # Backfill blog if the model omitted it (older prompt cache / rare failure).
    if "blog" not in parsed or not isinstance(parsed.get("blog"), dict):
        parsed["blog"] = {
            "title": _fallback_title(text),
            "content": parsed.get("linkedin") or text,
            "tags": [],
        }
    else:
        b = parsed["blog"]
        b.setdefault("title", _fallback_title(text))
        b.setdefault("content", parsed.get("linkedin") or text)
        b.setdefault("tags", [])
    return parsed


def rewrite_one(text: str, platform: str):
    """AI rewrite a single platform variant.

    For social platforms returns a string. For 'blog' returns a dict
    {title, content, tags}.
    """
    full = generate_variants(text)
    if platform == "blog":
        return full.get("blog") or {"title": _fallback_title(text), "content": text, "tags": []}
    return full.get(platform, "") or full.get("linkedin", "")
