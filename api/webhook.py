"""
Blog Bot v6.3.2 — Dashboard Mode + X Posting

Send a message to the bot and it shows your original text for every platform.
AI rewriting is optional — tap the button if you want it polished.

Platforms: Telegram channel, LinkedIn, X / Twitter.
Nothing auto-posts. You pick where to post with buttons.

Backlog: scheduling feature (not built yet).
"""

import os
import json
import logging
import time
import hashlib
import hmac
import base64
import urllib.parse
import secrets
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

# X / Twitter (OAuth 1.0a)
X_API_KEY = os.environ.get("X_API_KEY", "")
X_API_KEY_SECRET = os.environ.get("X_API_KEY_SECRET", "")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")

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

X_PROMPT = """Take this Telegram post and repurpose it as a SINGLE tweet for X (Twitter).

Rules:
- MUST be under 280 characters total. This is critical — count carefully.
- Condense ruthlessly: cut filler, use short words, remove anything non-essential
- Keep the casual, direct tone — no hashtags
- Punchy and complete in one tweet — no threads, no numbering
- If the original is very long, extract the ONE most compelling point

Original post:
---
{text}
---

Output ONLY the tweet text, nothing else. Must be under 280 characters."""

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
# Header prefixes used to separate label from draft content in messages
# ---------------------------------------------------------------------------

TG_HEADER = "*Telegram Channel:*\n\n"
LI_HEADER = "*LinkedIn:*\n\n"
X_HEADER = "*X / Twitter:*\n\n"

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
    """Post to LinkedIn. Returns True on success, or an error string on failure."""
    if not LINKEDIN_ACCESS_TOKEN:
        return "No LinkedIn access token configured."
    author_urn = get_linkedin_member_urn()
    if not author_urn:
        return "Could not resolve LinkedIn member URN."
    with httpx.Client(timeout=30) as client:
        # Try the newer Posts API first (v2/posts)
        resp = client.post(
            "https://api.linkedin.com/rest/posts",
            headers={
                "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
                "Content-Type": "application/json",
                "LinkedIn-Version": "202401",
                "X-Restli-Protocol-Version": "2.0.0",
            },
            json={
                "author": author_urn,
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
        logger.info(f"LinkedIn /rest/posts: {resp.status_code} {resp.text[:500]}")
        if resp.status_code in (200, 201):
            return True

        # Fallback to legacy ugcPosts API
        logger.info("Falling back to ugcPosts API...")
        resp2 = client.post(
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
        logger.info(f"LinkedIn ugcPosts: {resp2.status_code} {resp2.text[:500]}")
        if resp2.status_code in (200, 201):
            return True

        return f"API error {resp.status_code}: {resp.text[:200]}"


# ---------------------------------------------------------------------------
# X / Twitter posting (OAuth 1.0a)
# ---------------------------------------------------------------------------

def _percent_encode(s):
    return urllib.parse.quote(str(s), safe="")


def _build_oauth1_header(method, url, body_params=None):
    """Build OAuth 1.0a Authorization header for X API v2."""
    oauth_params = {
        "oauth_consumer_key": X_API_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": X_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }

    # Combine all params for signature base string
    all_params = {**oauth_params}
    if body_params:
        all_params.update(body_params)

    # Sort and encode
    sorted_params = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(all_params.items())
    )

    base_string = f"{method.upper()}&{_percent_encode(url)}&{_percent_encode(sorted_params)}"
    signing_key = f"{_percent_encode(X_API_KEY_SECRET)}&{_percent_encode(X_ACCESS_TOKEN_SECRET)}"

    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()

    oauth_params["oauth_signature"] = signature

    header = "OAuth " + ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return header


def post_to_x(text):
    """Post a tweet via X API v2. Returns True on success or error string."""
    if not all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET]):
        return "X API credentials not configured."

    url = "https://api.x.com/2/tweets"
    auth_header = _build_oauth1_header("POST", url)

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
            },
            json={"text": text},
        )
        logger.info(f"X API /2/tweets: {resp.status_code} {resp.text[:500]}")
        if resp.status_code in (200, 201):
            tweet_data = resp.json().get("data", {})
            tweet_id = tweet_data.get("id", "")
            return True
        return f"X API error {resp.status_code}: {resp.text[:200]}"


# ---------------------------------------------------------------------------
# Extract draft text from a message (strip the header line)
# ---------------------------------------------------------------------------

def extract_draft(message_text):
    """Strip the first-line header (e.g. '*Telegram Channel:*') and any trailing warnings."""
    if "\n\n" in message_text:
        draft = message_text.split("\n\n", 1)[1].strip()
    else:
        draft = message_text.strip()
    # Remove trailing warning notes (e.g. "⚠️ Over 280 chars...")
    if "\n\n\u26a0\ufe0f" in draft:
        draft = draft.split("\n\n\u26a0\ufe0f")[0].strip()
    return draft


# ---------------------------------------------------------------------------
# Dashboard: generate drafts & show buttons
# ---------------------------------------------------------------------------

def send_dashboard(chat_id, original_text):
    """Show original text for all platforms with post + optional AI rewrite buttons."""

    # --- Telegram channel draft (original text as-is) ---
    send_telegram_message(
        chat_id,
        f"{TG_HEADER}{original_text}",
        reply_markup={
            "inline_keyboard": [
                [{"text": "\U0001f4e2 Post to Channel", "callback_data": "post_tg"}],
                [{"text": "\u2728 AI Rewrite", "callback_data": "rewrite_tg"}],
            ]
        },
    )

    # --- LinkedIn draft (original text, note if over 3000 chars) ---
    li_note = ""
    if len(original_text) > 3000:
        li_note = "\n\n\u26a0\ufe0f Over 3000 chars — consider AI Rewrite to trim"
    li_action = [{"text": "\U0001f4bc Post to LinkedIn", "callback_data": "post_li"}]
    if not LINKEDIN_ACCESS_TOKEN:
        li_action = [{"text": "\U0001f4cb Copy for LinkedIn", "callback_data": "copy_li"}]
    send_telegram_message(
        chat_id,
        f"{LI_HEADER}{original_text}{li_note}",
        reply_markup={
            "inline_keyboard": [
                li_action,
                [{"text": "\u2728 AI Rewrite", "callback_data": "rewrite_li"}],
            ]
        },
    )

    # --- X draft (original text, warn if over 280 chars) ---
    x_note = ""
    if len(original_text) > 280:
        x_note = f"\n\n\u26a0\ufe0f {len(original_text)} chars — over 280 limit. Use AI Rewrite to fit."
    x_has_creds = all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET])
    x_action = [{"text": "\U0001f426 Post to X", "callback_data": "post_x"}] if x_has_creds else [{"text": "\U0001f426 Copy for X", "callback_data": "copy_x"}]
    send_telegram_message(
        chat_id,
        f"{X_HEADER}{original_text}{x_note}",
        reply_markup={
            "inline_keyboard": [
                x_action,
                [{"text": "\u2728 AI Rewrite", "callback_data": "rewrite_x"}],
            ]
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
        draft = extract_draft(message_text)
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
        draft = extract_draft(message_text)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "LI draft extraction failed"
        result = post_to_linkedin(draft)
        if result is True:
            edit_telegram_message(chat_id, message_id, "\u2705 Posted to LinkedIn!")
            answer_callback_query(callback_id, "Posted!")
            return "Posted to LinkedIn"
        else:
            error_detail = result if isinstance(result, str) else "Check token/credentials."
            edit_telegram_message(chat_id, message_id, f"Failed to post to LinkedIn. {error_detail}")
            answer_callback_query(callback_id, "Failed")
            return f"LinkedIn post failed: {error_detail}"

    # --- Post to X ---
    if data == "post_x":
        draft = extract_draft(message_text)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "X draft extraction failed"
        answer_callback_query(callback_id, "Posting to X...")
        try:
            result = post_to_x(draft)
            if result is True:
                edit_telegram_message(chat_id, message_id, "\u2705 Posted to X!")
                return "Posted to X"
            else:
                error_detail = result if isinstance(result, str) else "Check credentials."
                edit_telegram_message(chat_id, message_id, f"Failed to post to X. {error_detail}")
                return f"X post failed: {error_detail}"
        except Exception as e:
            logger.error(f"X post exception: {e}", exc_info=True)
            edit_telegram_message(chat_id, message_id, f"Failed to post to X. Error: {str(e)[:200]}")
            return f"X post exception: {e}"

    # --- Copy confirmations (just acknowledge) ---
    if data == "copy_li":
        answer_callback_query(callback_id, "Copy the text above and paste it into LinkedIn!")
        return "LinkedIn copy hint"

    if data == "copy_x":
        answer_callback_query(callback_id, "Copy the text above and paste it into X!")
        return "X copy hint"

    # --- AI Rewrite handlers ---
    if data == "rewrite_tg":
        draft = extract_draft(message_text)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "TG rewrite extraction failed"
        answer_callback_query(callback_id, "Rewriting with AI...")
        try:
            rewritten = repurpose_for_telegram(draft)
            edit_telegram_message(
                chat_id, message_id,
                f"{TG_HEADER}{rewritten}",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "\U0001f4e2 Post to Channel", "callback_data": "post_tg"}],
                    ]
                },
            )
            return "TG rewritten"
        except Exception as e:
            logger.error(f"TG rewrite failed: {e}", exc_info=True)
            answer_callback_query(callback_id, "Rewrite failed")
            return f"TG rewrite error: {e}"

    if data == "rewrite_li":
        draft = extract_draft(message_text)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "LI rewrite extraction failed"
        answer_callback_query(callback_id, "Rewriting with AI...")
        try:
            rewritten = repurpose_for_linkedin(draft)
            li_action = [{"text": "\U0001f4bc Post to LinkedIn", "callback_data": "post_li"}]
            if not LINKEDIN_ACCESS_TOKEN:
                li_action = [{"text": "\U0001f4cb Copy for LinkedIn", "callback_data": "copy_li"}]
            edit_telegram_message(
                chat_id, message_id,
                f"{LI_HEADER}{rewritten}",
                reply_markup={"inline_keyboard": [li_action]},
            )
            return "LI rewritten"
        except Exception as e:
            logger.error(f"LI rewrite failed: {e}", exc_info=True)
            answer_callback_query(callback_id, "Rewrite failed")
            return f"LI rewrite error: {e}"

    if data == "rewrite_x":
        draft = extract_draft(message_text)
        if not draft:
            answer_callback_query(callback_id, "Could not find draft text.")
            return "X rewrite extraction failed"
        answer_callback_query(callback_id, "Rewriting with AI...")
        try:
            rewritten = repurpose_for_x(draft)
            x_has_creds = all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET])
            x_action = [{"text": "\U0001f426 Post to X", "callback_data": "post_x"}] if x_has_creds else [{"text": "\U0001f426 Copy for X", "callback_data": "copy_x"}]
            edit_telegram_message(
                chat_id, message_id,
                f"{X_HEADER}{rewritten}",
                reply_markup={
                    "inline_keyboard": [x_action]
                },
            )
            return "X rewritten"
        except Exception as e:
            logger.error(f"X rewrite failed: {e}", exc_info=True)
            answer_callback_query(callback_id, "Rewrite failed")
            return f"X rewrite error: {e}"

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
        x_status = "connected" if all([X_API_KEY, X_ACCESS_TOKEN]) else "not connected (copy-paste mode)"
        send_telegram_message(
            chat_id,
            f"Hey {first_name}!\n\n"
            "Send me any text and I'll show it ready for:\n"
            "\u2022 *Telegram* channel\n"
            "\u2022 *LinkedIn*\n"
            "\u2022 *X / Twitter*\n\n"
            "Your text posts as-is by default.\n"
            "Tap \u2728 *AI Rewrite* if you want it polished.\n"
            "Nothing posts automatically.\n\n"
            f"LinkedIn: {li_status}\n"
            f"X: {x_status}",
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

        send_telegram_message(owner_id, "Detected your channel post! Ready for other platforms.")

        # LinkedIn draft (original text)
        li_note = ""
        if len(text) > 3000:
            li_note = "\n\n\u26a0\ufe0f Over 3000 chars — consider AI Rewrite to trim"
        li_action = [{"text": "\U0001f4bc Post to LinkedIn", "callback_data": "post_li"}]
        if not LINKEDIN_ACCESS_TOKEN:
            li_action = [{"text": "\U0001f4cb Copy for LinkedIn", "callback_data": "copy_li"}]
        send_telegram_message(
            owner_id,
            f"{LI_HEADER}{text}{li_note}",
            reply_markup={
                "inline_keyboard": [
                    li_action,
                    [{"text": "\u2728 AI Rewrite", "callback_data": "rewrite_li"}],
                ]
            },
        )

        # X draft (original text)
        x_note = ""
        if len(text) > 280:
            x_note = f"\n\n\u26a0\ufe0f {len(text)} chars — over 280 limit. Use AI Rewrite to fit."
        x_has_creds = all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET])
        x_action = [{"text": "\U0001f426 Post to X", "callback_data": "post_x"}] if x_has_creds else [{"text": "\U0001f426 Copy for X", "callback_data": "copy_x"}]
        send_telegram_message(
            owner_id,
            f"{X_HEADER}{text}{x_note}",
            reply_markup={
                "inline_keyboard": [
                    x_action,
                    [{"text": "\u2728 AI Rewrite", "callback_data": "rewrite_x"}],
                ]
            },
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
            json.dumps({"status": "alive", "bot": "blog-bot", "version": "6.3.2"}).encode()
        )
