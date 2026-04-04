"""
Blog Bot — Telegram Webhook Handler (Vercel Serverless Function)

Receives text messages from Telegram, generates a blog post with Claude,
and publishes to your Telegram channel.
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


BLOG_SYSTEM_PROMPT = """You are a personal blog ghostwriter. Your job is to take raw notes or
ideas and transform them into a compelling, well-structured blog post that sounds like the
author wrote it themselves.

Rules:
1. PRESERVE the author's authentic voice, tone, and personality.
2. Structure the content with a hook opening, clear sections, and a strong closing.
3. Fix grammar but keep the conversational feel.
4. Use short paragraphs and line breaks for easy reading on mobile/Telegram.
5. Add relevant emojis sparingly if it fits the tone (1-3 per post max).
6. Keep the length proportional to the content.
7. Format for Telegram: use bold (*text*) for emphasis, keep paragraphs short.
8. End with a thought-provoking question or call-to-action when appropriate.
9. Do NOT add a title/headline.
10. Do NOT use markdown headers (#). Use bold text (*text*) for section breaks if needed."""

BLOG_USER_PROMPT = """Here is the raw text to transform into a blog post:

---
{text}
---

Transform this into a polished blog post that preserves my authentic voice.
Output ONLY the blog post text, nothing else."""


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


def generate_blog_post(text):
    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=BLOG_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": BLOG_USER_PROMPT.format(text=text)}
        ],
    )
    return message.content[0].text.strip()


def post_to_channel(blog_text):
    if len(blog_text) <= 4096:
        return send_telegram_message(TELEGRAM_CHANNEL_ID, blog_text)
    chunks = split_text(blog_text, max_length=4096)
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

    text = message.get("text", "")

    if text.startswith("/start"):
        send_telegram_message(
            chat_id,
            "Hey " + first_name + "!\n\n"
            "Send me a text message with your ideas, thoughts, or notes "
            "and I'll turn it into a polished blog post "
            "and publish it to your channel.\n\n"
            "*Commands:*\n"
            "/start - Show this message",
        )
        return "Start command handled"

    if not text or text.startswith("/"):
        send_telegram_message(
            chat_id,
            "Send me a text message with your ideas and I'll turn it into a blog post!",
        )
        return "No text content"

    try:
        send_telegram_message(chat_id, "Writing your blog post...")
        blog_post = generate_blog_post(text)
        post_to_channel(blog_post)
        preview = blog_post[:1000] + ("..." if len(blog_post) > 1000 else "")
        send_telegram_message(
            chat_id,
            "*Posted to your channel!*\n\n"
            "*Blog post preview:*\n" + preview,
        )
        return "Blog posted successfully"
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
        self.wfile.write(json.dumps({"status": "alive", "bot": "blog-bot"}).encode())
