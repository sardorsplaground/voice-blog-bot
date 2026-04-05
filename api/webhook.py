"""
Blog Bot v3.0 — Post to Telegram + Repurpose for X & LinkedIn

Posts the user's exact text to their Telegram channel, then uses Claude
to repurpose it for X (Twitter) and LinkedIn, sending the adapted versions
back to the user for easy copy-paste.
"""

import os
import json
import logging
from http.server import BaseHTTPRequestHandler

import httpx
import anthropic

# Config
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USER_IDS = os.environ.get("ALLOWED_USER_IDS", "")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("blog-bot")

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

REPURPOSE_SYSTEM = """You repurpose Telegram channel posts for other platforms.
Keep the author's voice exactly as-is — confident, casual, direct, founder energy.
Never add hashtags. Never sound like a marketer. Keep it real."""

X_PROMPT = """Take this Telegram post and repurpose it for X (Twitter).

Rules:
- If it fits in one tweet (under 280 chars), make it one tweet
- If it needs a thread, break it into numbered tweets (1/, 2/, etc.)
- Each tweet must be under 280 characters
- Keep the casual, direct tone — no hashtags
- First tweet should hook people in
- Cut any filler — X rewards punchy writing

Original post:
---
{text}
---

Output ONLY the tweet(s). If it's a thread, separate tweets with a blank line."""

LINKEDIN_PROMPT = """Take this Telegram post and repurpose it for LinkedIn.

Rules:
- Keep it under 1300 characters (LinkedIn sweet spot)
- First line should be a hook that makes people click "see more"
- Use line breaks between paragraphs for readability
- Keep the founder voice — don't make it corporate or cringe
- No hashtags, no emojis spam, no "I'm humbled" energy
- End with a question or bold statement to drive engagement

Original post:
---
{text}
---

Output ONLY the LinkedIn post text, nothing else."""


def is_user_allowed(user_id):
    if not ALLOWED_USER_IDS.strip():
        return True
    allowed = [int(uid.strip()) for uid in ALLOWED_USER_IDS.split(",") if uid.strip()]
    return user_id in allowed


def send_telegram_message(chat_id, text, parse_mode="Markdown"):
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        )
        resp.raise_for_status()
        return resp.json()


def post_to_channel(text):
    if len(text) <= 4096:
        return send_telegram_message(TELEGRAM_CHANNEL_ID, text)
    chunks = split_text(text, max_length=4096)
    result = None
    for chunk in chunks:
        result = send_telegram_message(TELEGRAM_CHANNEL_ID, chunk)
    return result


def split_text(text, max_length=4096):
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > max_length:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            current_chunk = current_chunk + "\n\n" + para if current_chunk else para
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks


def repurpose_for_x(text):
    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=REPURPOSE_SYSTEM,
        messages=[{"role": "user", "content": X_PROMPT.format(text=text)}],
    )
    return message.content[0].text.strip()


def repurpose_for_linkedin(text):
    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=REPURPOSE_SYSTEM,
        messages=[{"role": "user", "content": LINKEDIN_PROMPT.format(text=text)}],
    )
    return message.content[0].text.strip()


def process_update(update):
    message = update.get("message")
    if not message:
        return "No message in update"

    user_id = message["from"]["id"]
    chat_id = message["chat"]["id"]
    first_name = message["from"].get("first_name", "there")

    if not is_user_allowed(user_id):
        send_telegram_message(chat_id, "Sorry, you're not authorized to use this bot.")
        return "Unauthorized user"

    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        send_telegram_message(
            chat_id,
            "Hey " + first_name + "!\n\n"
            "Send me any text and I'll:\n"
            "1. Post it *exactly as you wrote it* to your channel\n"
            "2. Repurpose it for *X* and *LinkedIn*\n\n"
            "Just send me your post and I'll handle the rest.",
        )
        return "Start command handled"

    if not text or text.startswith("/"):
        send_telegram_message(chat_id, "Send me your post and I'll publish + repurpose it!")
        return "No text content"

    try:
        # Step 1: Post exact text to Telegram channel
        post_to_channel(text)
        send_telegram_message(chat_id, "Posted to your channel!")

        # Step 2: Repurpose for X and LinkedIn
        send_telegram_message(chat_id, "Repurposing for X and LinkedIn...")

        x_version = repurpose_for_x(text)
        linkedin_version = repurpose_for_linkedin(text)

        # Send X version
        send_telegram_message(
            chat_id,
            "*For X:*\n\n" + x_version,
        )

        # Send LinkedIn version
        send_telegram_message(
            chat_id,
            "*For LinkedIn:*\n\n" + linkedin_version,
        )

        return "Posted and repurposed"

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        send_telegram_message(chat_id, f"Something went wrong: {str(e)[:200]}")
        return f"Error: {e}"


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            update = json.loads(body)
            result = process_update(update)
            logger.info(f"Processed update: {result}")
        except Exception as e:
            logger.error(f"Failed to process update: {e}", exc_info=True)
            result = f"Error: {e}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "result": result}).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"status": "alive", "bot": "blog-bot", "version": "3.0"}).encode()
        )
