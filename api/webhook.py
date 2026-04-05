"""
Blog Bot v5.0 — Post to Telegram + LinkedIn (with approval) + Repurpose for X

- DM the bot: posts your exact text to your Telegram channel, then repurposes
- Post directly in your channel: bot detects it and repurposes automatically
- LinkedIn draft is sent for your approval before posting
- X version is always sent as copy-paste in the bot chat
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

# LinkedIn (optional — set these once you have API access)
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_PERSON_URN = os.environ.get("LINKEDIN_PERSON_URN", "")  # e.g. "urn:li:person:abc123"

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

# --- Marker used to delimit the LinkedIn draft in messages ---
DRAFT_MARKER = "--- LINKEDIN DRAFT ---"
DRAFT_END_MARKER = "--- END DRAFT ---"


def is_user_allowed(user_id):
    if not ALLOWED_USER_IDS.strip():
        return True
    allowed = [int(uid.strip()) for uid in ALLOWED_USER_IDS.split(",") if uid.strip()]
    return user_id in allowed


def send_telegram_message(chat_id, text, parse_mode="Markdown", reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
        resp.raise_for_status()
        return resp.json()


def edit_telegram_message(chat_id, message_id, text, parse_mode="Markdown", reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{TELEGRAM_API}/editMessageText", json=payload)
        resp.raise_for_status()
        return resp.json()


def answer_callback_query(callback_query_id, text=""):
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
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


def post_to_linkedin(text):
    """Post text to LinkedIn using the Posts API. Returns True on success."""
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        return False
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            "https://api.linkedin.com/rest/posts",
            headers={
                "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
                "Content-Type": "application/json",
                "LinkedIn-Version": "202504",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "author": LINKEDIN_PERSON_URN,
                "commentary": text,
                "visibility": "PUBLIC",
                "distribution": {
                    "feedDistribution": "MAIN_FEED",
                    "targetEntities": [],
                    "thirdPartyDistributionChannels": [],
                },
                "lifecycleState": "PUBLISHED",
                "isReshareDisabledByAuthor": False,
            },
        )
        if resp.status_code in (200, 201):
            logger.info("Posted to LinkedIn successfully")
            return True
        else:
            logger.error(f"LinkedIn post failed: {resp.status_code} {resp.text}")
            return False


def send_linkedin_draft(chat_id, linkedin_version):
    """Send the LinkedIn draft with approve/skip inline keyboard buttons."""
    draft_message = (
        f"*LinkedIn Draft:*\n\n"
        f"{linkedin_version}\n\n"
        f"{DRAFT_MARKER}\n{linkedin_version}\n{DRAFT_END_MARKER}"
    )

    inline_keyboard = {
        "inline_keyboard": [
            [
                {"text": "Post to LinkedIn", "callback_data": "post_li"},
                {"text": "Skip", "callback_data": "skip_li"},
            ]
        ]
    }

    return send_telegram_message(chat_id, draft_message, reply_markup=inline_keyboard)


def extract_draft_from_message(message_text):
    """Extract the LinkedIn draft text from between the markers."""
    if DRAFT_MARKER not in message_text or DRAFT_END_MARKER not in message_text:
        return None
    start = message_text.index(DRAFT_MARKER) + len(DRAFT_MARKER)
    end = message_text.index(DRAFT_END_MARKER)
    return message_text[start:end].strip()


def process_callback_query(update):
    """Handle inline keyboard button presses (approve/skip LinkedIn)."""
    callback = update.get("callback_query")
    if not callback:
        return "No callback_query"

    callback_id = callback["id"]
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    message_text = message.get("text", "")

    if data == "post_li":
        # Extract the draft from the message
        draft_text = extract_draft_from_message(message_text)
        if not draft_text:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "Draft extraction failed"

        # Post to LinkedIn
        success = post_to_linkedin(draft_text)

        if success:
            edit_telegram_message(chat_id, message_id, "Posted to LinkedIn!")
            answer_callback_query(callback_id, "Posted!")
            return "LinkedIn post approved and published"
        else:
            edit_telegram_message(
                chat_id, message_id,
                "Failed to post to LinkedIn. Check your token/credentials."
            )
            answer_callback_query(callback_id, "Post failed")
            return "LinkedIn post failed"

    elif data == "skip_li":
        edit_telegram_message(chat_id, message_id, "LinkedIn post skipped.")
        answer_callback_query(callback_id, "Skipped")
        return "LinkedIn post skipped"

    else:
        answer_callback_query(callback_id)
        return f"Unknown callback: {data}"


def process_channel_post(update):
    """Handle posts made directly in the channel."""
    channel_post = update.get("channel_post")
    if not channel_post:
        return "No channel_post"

    text = (channel_post.get("text") or "").strip()
    if not text:
        return "No text in channel post"

    # Don't process bot's own posts (avoid infinite loop)
    sender = channel_post.get("from", {})
    if sender.get("is_bot", False):
        return "Skipping bot's own post"

    try:
        owner_id = int(ALLOWED_USER_IDS.split(",")[0].strip())

        send_telegram_message(owner_id, "Detected your channel post! Repurposing for X and LinkedIn...")

        x_version = repurpose_for_x(text)
        linkedin_version = repurpose_for_linkedin(text)

        # X version (always copy-paste)
        send_telegram_message(owner_id, "*For X:*\n\n" + x_version)

        # LinkedIn version — send as draft for approval
        if LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN:
            send_linkedin_draft(owner_id, linkedin_version)
        else:
            send_telegram_message(owner_id, "*For LinkedIn (copy-paste):*\n\n" + linkedin_version)

        return "Channel post repurposed"
    except Exception as e:
        logger.error(f"Error processing channel post: {e}", exc_info=True)
        return f"Error: {e}"


def process_dm(update):
    """Handle direct messages to the bot."""
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
        linkedin_status = "connected (approval mode)" if LINKEDIN_ACCESS_TOKEN else "not connected (copy-paste mode)"
        send_telegram_message(
            chat_id,
            "Hey " + first_name + "!\n\n"
            "Send me any text and I'll:\n"
            "1. Post it *exactly as you wrote it* to your channel\n"
            "2. Repurpose it for *X* and *LinkedIn*\n\n"
            "LinkedIn posts will be sent as a *draft for your approval* before publishing.\n\n"
            "You can also post directly to your channel — "
            "I'll detect it and repurpose automatically.\n\n"
            f"LinkedIn: {linkedin_status}",
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

        # Step 3: Send X version (always copy-paste)
        send_telegram_message(chat_id, "*For X:*\n\n" + x_version)

        # Step 4: Send LinkedIn draft for approval
        if LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN:
            send_linkedin_draft(chat_id, linkedin_version)
        else:
            send_telegram_message(chat_id, "*For LinkedIn (copy-paste):*\n\n" + linkedin_version)

        return "Posted and repurposed"
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        send_telegram_message(chat_id, f"Something went wrong: {str(e)[:200]}")
        return f"Error: {e}"


def process_update(update):
    """Route update to the right handler."""
    if "callback_query" in update:
        return process_callback_query(update)
    elif "channel_post" in update:
        return process_channel_post(update)
    elif "message" in update:
        return process_dm(update)
    else:
        return "Unknown update type"


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
            json.dumps({"status": "alive", "bot": "blog-bot", "version": "5.0"}).encode()
        )

