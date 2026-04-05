"""
Blog Bot v6.0 — Dashboard Mode

Send a message to the bot and it formats your content for every platform:
  - Telegram channel (original text)
  - LinkedIn (repurposed)
  - X / Twitter (repurposed)

Then shows buttons so you pick where to post. Nothing auto-posts.

Backlog: scheduling feature (not built yet).
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

# LinkedIn
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_PERSON_URN = os.environ.get("LINKEDIN_PERSON_URN", "")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("blog-bot")

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

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

TELEGRAM_PROMPT = """Take this raw text and format it as a polished Telegram channel post.

Rules:
- Keep the author's exact voice — confident, casual, direct
- You may lightly clean up formatting (line breaks, emphasis) but do NOT rewrite
- Use Telegram-friendly markdown: *bold*, _italic_
- Keep the length roughly the same — don't pad it, don't cut substance
- If it already looks good for Telegram, return it almost unchanged
- Never add hashtags

Original text:
---
{text}
---

Output ONLY the formatted Telegram post, nothing else."""

# ---------------------------------------------------------------------------
# Markers for embedding draft text in messages
# ---------------------------------------------------------------------------

TG_DRAFT_MARKER = "---TG_DRAFT---"
TG_DRAFT_END = "---/TG_DRAFT---"
LI_DRAFT_MARKER = "---LI_DRAFT---"
LI_DRAFT_END = "---/LI_DRAFT---"
X_DRAFT_MARKER = "---X_DRAFT---"
X_DRAFT_END = "---/X_DRAFT---"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def split_text(text, max_length=4096):
    paragraphs = text.split("\n\n")
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > max_length:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current:
        chunks.append(current.strip())
    return chunks


def post_to_channel(text):
    if len(text) <= 4096:
        return send_telegram_message(TELEGRAM_CHANNEL_ID, text)
    chunks = split_text(text, max_length=4096)
    result = None
    for chunk in chunks:
        result = send_telegram_message(TELEGRAM_CHANNEL_ID, chunk)
    return result


# ---------------------------------------------------------------------------
# Claude repurposing
# ---------------------------------------------------------------------------

def repurpose_for_telegram(text):
    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=REPURPOSE_SYSTEM,
        messages=[{"role": "user", "content": TELEGRAM_PROMPT.format(text=text)}],
    )
    return message.content[0].text.strip()


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


# ---------------------------------------------------------------------------
# LinkedIn posting
# ---------------------------------------------------------------------------

def get_linkedin_member_urn():
    if not LINKEDIN_ACCESS_TOKEN:
        return None
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"},
        )
        logger.info(f"LinkedIn /v2/userinfo: {resp.status_code}")
        if resp.status_code == 200:
            sub = resp.json().get("sub")
            if sub:
                return f"urn:li:person:{sub}"
        resp = client.get(
            "https://api.linkedin.com/v2/me",
            headers={"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"},
        )
        if resp.status_code == 200:
            mid = resp.json().get("id")
            if mid:
                return f"urn:li:person:{mid}"
    if LINKEDIN_PERSON_URN:
        return LINKEDIN_PERSON_URN
    return None


def post_to_linkedin(text):
    if not LINKEDIN_ACCESS_TOKEN:
        return False
    author_urn = get_linkedin_member_urn()
    if not author_urn:
        return False
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers={
                "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                },
            },
        )
        logger.info(f"LinkedIn post: {resp.status_code} {resp.text[:300]}")
        return resp.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Extract embedded draft text from a message
# ---------------------------------------------------------------------------

def extract_between(text, start_marker, end_marker):
    if start_marker not in text or end_marker not in text:
        return None
    s = text.index(start_marker) + len(start_marker)
    e = text.index(end_marker)
    return text[s:e].strip()


# ---------------------------------------------------------------------------
# Dashboard: generate drafts & show buttons
# ---------------------------------------------------------------------------

def send_dashboard(chat_id, original_text):
    """Format content for all platforms and send drafts with action buttons."""
    send_telegram_message(chat_id, "Formatting your post for all platforms...")

    tg_version = repurpose_for_telegram(original_text)
    li_version = repurpose_for_linkedin(original_text)
    x_version = repurpose_for_x(original_text)

    # --- Telegram channel draft ---
    tg_msg = (
        f"*Telegram Channel:*\n\n"
        f"{tg_version}\n\n"
        f"{TG_DRAFT_MARKER}\n{tg_version}\n{TG_DRAFT_END}"
    )
    send_telegram_message(
        chat_id,
        tg_msg,
        reply_markup={
            "inline_keyboard": [[
                {"text": "\U0001f4e2 Post to Channel", "callback_data": "post_tg"},
            ]]
        },
    )

    # --- LinkedIn draft ---
    li_msg = (
        f"*LinkedIn:*\n\n"
        f"{li_version}\n\n"
        f"{LI_DRAFT_MARKER}\n{li_version}\n{LI_DRAFT_END}"
    )
    li_buttons = [[{"text": "\U0001f4bc Post to LinkedIn", "callback_data": "post_li"}]]
    if not LINKEDIN_ACCESS_TOKEN:
        li_buttons = [[{"text": "\U0001f4cb Copy for LinkedIn", "callback_data": "copy_li"}]]
    send_telegram_message(
        chat_id,
        li_msg,
        reply_markup={"inline_keyboard": li_buttons},
    )

    # --- X draft (always copy-paste for now) ---
    x_msg = (
        f"*X / Twitter:*\n\n"
        f"{x_version}\n\n"
        f"{X_DRAFT_MARKER}\n{x_version}\n{X_DRAFT_END}"
    )
    send_telegram_message(
        chat_id,
        x_msg,
        reply_markup={
            "inline_keyboard": [[
                {"text": "\U0001f426 Copy for X", "callback_data": "copy_x"},
            ]]
        },
    )

    send_telegram_message(chat_id, "Pick where you want to post \u2191")


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

def process_callback_query(update):
    callback = update.get("callback_query")
    if not callback:
        return "No callback"

    callback_id = callback["id"]
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    message_text = message.get("text", "")

    # --- Post to Telegram channel ---
    if data == "post_tg":
        draft = extract_between(message_text, TG_DRAFT_MARKER, TG_DRAFT_END)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "TG draft extraction failed"
        try:
            post_to_channel(draft)
            edit_telegram_message(chat_id, message_id, "\u2705 Posted to Telegram channel!")
            answer_callback_query(callback_id, "Posted!")
            return "Posted to channel"
        except Exception as e:
            logger.error(f"Channel post failed: {e}", exc_info=True)
            edit_telegram_message(chat_id, message_id, f"Failed to post to channel: {str(e)[:200]}")
            answer_callback_query(callback_id, "Failed")
            return f"Channel post error: {e}"

    # --- Post to LinkedIn ---
    if data == "post_li":
        draft = extract_between(message_text, LI_DRAFT_MARKER, LI_DRAFT_END)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "LI draft extraction failed"
        success = post_to_linkedin(draft)
        if success:
            edit_telegram_message(chat_id, message_id, "\u2705 Posted to LinkedIn!")
            answer_callback_query(callback_id, "Posted!")
            return "Posted to LinkedIn"
        else:
            edit_telegram_message(chat_id, message_id, "Failed to post to LinkedIn. Check token/credentials.")
            answer_callback_query(callback_id, "Failed")
            return "LinkedIn post failed"

    # --- Copy confirmations (just acknowledge) ---
    if data == "copy_li":
        answer_callback_query(callback_id, "Copy the text above and paste it into LinkedIn!")
        return "LinkedIn copy hint"

    if data == "copy_x":
        answer_callback_query(callback_id, "Copy the text above and paste it into X!")
        return "X copy hint"

    answer_callback_query(callback_id)
    return f"Unknown callback: {data}"


# ---------------------------------------------------------------------------
# DM handler
# ---------------------------------------------------------------------------

def process_dm(update):
    message = update.get("message")
    if not message:
        return "No message"

    user_id = message["from"]["id"]
    chat_id = message["chat"]["id"]
    first_name = message["from"].get("first_name", "there")

    if not is_user_allowed(user_id):
        send_telegram_message(chat_id, "Sorry, you're not authorized to use this bot.")
        return "Unauthorized"

    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        li_status = "connected" if LINKEDIN_ACCESS_TOKEN else "not connected (copy-paste mode)"
        send_telegram_message(
            chat_id,
            f"Hey {first_name}!\n\n"
            "Send me any text and I'll format it for:\n"
            "\u2022 *Telegram* channel\n"
            "\u2022 *LinkedIn*\n"
            "\u2022 *X / Twitter*\n\n"
            "Then you pick where to post with buttons.\n"
            "Nothing posts automatically.\n\n"
            f"LinkedIn: {li_status}",
        )
        return "Start"

    if not text or text.startswith("/"):
        send_telegram_message(chat_id, "Send me your post and I'll format it for every platform!")
        return "No text"

    try:
        send_dashboard(chat_id, text)
        return "Dashboard sent"
    except Exception as e:
        logger.error(f"Dashboard error: {e}", exc_info=True)
        send_telegram_message(chat_id, f"Something went wrong: {str(e)[:200]}")
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Channel post handler (repurpose posts made directly in the channel)
# ---------------------------------------------------------------------------

def process_channel_post(update):
    channel_post = update.get("channel_post")
    if not channel_post:
        return "No channel_post"

    text = (channel_post.get("text") or "").strip()
    if not text:
        return "No text"

    sender = channel_post.get("from", {})
    if sender.get("is_bot", False):
        return "Skipping bot post"

    try:
        owner_id = int(ALLOWED_USER_IDS.split(",")[0].strip())

        send_telegram_message(owner_id, "Detected your channel post! Formatting for other platforms...")

        li_version = repurpose_for_linkedin(text)
        x_version = repurpose_for_x(text)

        # LinkedIn draft
        li_msg = (
            f"*LinkedIn:*\n\n"
            f"{li_version}\n\n"
            f"{LI_DRAFT_MARKER}\n{li_version}\n{LI_DRAFT_END}"
        )
        li_buttons = [[{"text": "\U0001f4bc Post to LinkedIn", "callback_data": "post_li"}]]
        if not LINKEDIN_ACCESS_TOKEN:
            li_buttons = [[{"text": "\U0001f4cb Copy for LinkedIn", "callback_data": "copy_li"}]]
        send_telegram_message(owner_id, li_msg, reply_markup={"inline_keyboard": li_buttons})

        # X draft
        x_msg = (
            f"*X / Twitter:*\n\n"
            f"{x_version}\n\n"
            f"{X_DRAFT_MARKER}\n{x_version}\n{X_DRAFT_END}"
        )
        send_telegram_message(
            owner_id,
            x_msg,
            reply_markup={"inline_keyboard": [[{"text": "\U0001f426 Copy for X", "callback_data": "copy_x"}]]},
        )

        return "Channel post repurposed"
    except Exception as e:
        logger.error(f"Channel post error: {e}", exc_info=True)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def process_update(update):
    if "callback_query" in update:
        return process_callback_query(update)
    elif "channel_post" in update:
        return process_channel_post(update)
    elif "message" in update:
        return process_dm(update)
    return "Unknown update type"


# ---------------------------------------------------------------------------
# Vercel handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            update = json.loads(body)
            result = process_update(update)
            logger.info(f"Processed: {result}")
        except Exception as e:
            logger.error(f"Failed: {e}", exc_info=True)
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
            json.dumps({"status": "alive", "bot": "blog-bot", "version": "6.0"}).encode()
        )
