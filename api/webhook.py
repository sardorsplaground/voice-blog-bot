"""
Blog Bot v2.0 — Draft-first Telegram Blog Bot (Vercel Serverless Function)

Receives text messages from Telegram, generates a draft blog post with Claude
matching the author's voice, sends it for review, and publishes on approval.
"""

import os
import json
import logging
import time
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

DRAFT_FILE = "/tmp/blog_bot_drafts.json"
FEEDBACK_FILE = "/tmp/blog_bot_feedback.json"


def _load_drafts():
    try:
        with open(DRAFT_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_drafts(drafts):
    with open(DRAFT_FILE, "w") as f:
        json.dump(drafts, f)


def get_draft(user_id):
    drafts = _load_drafts()
    return drafts.get(str(user_id))


def save_draft(user_id, draft_data):
    drafts = _load_drafts()
    drafts[str(user_id)] = draft_data
    _save_drafts(drafts)


def clear_draft(user_id):
    drafts = _load_drafts()
    drafts.pop(str(user_id), None)
    _save_drafts(drafts)


def _load_feedback():
    try:
        with open(FEEDBACK_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_feedback(feedback_list):
    with open(FEEDBACK_FILE, "w") as f:
        json.dump(feedback_list, f)


def add_feedback(feedback_text):
    fb = _load_feedback()
    fb.append({"feedback": feedback_text, "ts": int(time.time())})
    fb = fb[-20:]
    _save_feedback(fb)


def get_feedback_context():
    fb = _load_feedback()
    if not fb:
        return ""
    lines = [f"- {entry['feedback']}" for entry in fb[-10:]]
    return (
        "\n\nIMPORTANT \u2014 The author has given you this feedback on previous drafts. "
        "Apply these lessons to ALL future drafts:\n" + "\n".join(lines)
    )


VOICE_PROFILE = """
AUTHOR VOICE PROFILE \u2014 Sardor Akhmedov (@akhmedovco)

You are ghostwriting for Sardor. Here is exactly how he writes:

TONE & PERSONALITY:
- Confident, direct, opinionated \u2014 not afraid of hot takes
- Conversational and casual \u2014 writes like he's talking to a friend
- Founder/CEO energy \u2014 speaks from real experience running a company
- Optimistic about AI and building \u2014 genuinely excited, not performative
- Uses casual abbreviations: "rn" for "right now", "cuz", "biz"
- Occasionally self-deprecating humor

STRUCTURE (this is critical):
- Opens with a bold, declarative statement \u2014 NO preamble or throat-clearing
- Short paragraphs \u2014 usually 1-3 sentences each
- Total post length: typically 3-8 short paragraphs (NOT long essays)
- Sometimes uses bullet points with \u2022 for lists
- Ends with a question OR a punchy closing statement \u2014 not both
- NO section headers, NO titles, NO "here's why this matters" transitions
- NO formal blog structure \u2014 this is a Telegram channel post, not a Medium article

CONTENT PATTERNS:
- Shares real numbers ($10k/m, $900/m, $9,500) \u2014 very transparent
- Names specific tools, companies, people \u2014 never vague
- Ties everything back to personal experience or his company (Bolder Apps / Synergy Labs)
- Topics: AI, vibecoding, entrepreneurship, SaaS, building products
- Makes predictions and bold claims, then backs them with examples
- When sharing links, adds brief punchy commentary \u2014 not a full writeup

FORMATTING:
- Uses *bold* for emphasis (Telegram Markdown)
- Emojis: very sparingly, 0-2 per post max, usually none
- Line breaks between paragraphs for mobile readability
- Keep it under 300 words unless the raw material truly warrants more

WHAT TO AVOID:
- Don't sound like a "content creator" or marketer
- Don't add motivational fluff or generic advice
- Don't use formal transitions ("Furthermore", "In conclusion", "Here's the thing")
- Don't write section headers or subheadings
- Don't ask multiple questions \u2014 one max, at the end
- Don't pad with filler \u2014 every sentence should carry weight
- Don't use hashtags
"""

BLOG_SYSTEM_PROMPT = (
    "You are Sardor Akhmedov's personal ghostwriter for his Telegram channel @akhmedovco."
    "\n\nYour ONLY job: take his raw notes/ideas and turn them into a post that sounds "
    "exactly like he wrote it himself. Match his voice perfectly."
    "\n\n"
    + VOICE_PROFILE
)

BLOG_USER_PROMPT = """Raw notes to transform into a channel post:
---
{text}
---
Transform this into a Telegram channel post matching my voice exactly. Output ONLY the post text, nothing else."""

REVISE_USER_PROMPT = """Here is the previous draft you wrote:
---
{draft}
---

The author wants you to revise it with this feedback:
---
{feedback}
---

Rewrite the post incorporating the feedback while keeping my voice. Output ONLY the revised post text, nothing else."""


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
    system = BLOG_SYSTEM_PROMPT + get_feedback_context()
    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": BLOG_USER_PROMPT.format(text=text)}],
    )
    return message.content[0].text.strip()


def revise_blog_post(draft, feedback):
    system = BLOG_SYSTEM_PROMPT + get_feedback_context()
    message = claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system,
        messages=[
            {"role": "user", "content": REVISE_USER_PROMPT.format(draft=draft, feedback=feedback)}
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

    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        send_telegram_message(
            chat_id,
            "Hey " + first_name + "!\n\n"
            "Send me your ideas or notes and I'll draft a blog post "
            "matching your voice.\n\n"
            "I'll show you the draft first \u2014 you decide when to publish.\n\n"
            "*Commands:*\n"
            "`post` \u2014 publish the current draft\n"
            "`reject` \u2014 discard the draft\n"
            "Or just reply with feedback to revise it",
        )
        return "Start command handled"

    if not text or text.startswith("/"):
        send_telegram_message(chat_id, "Send me your ideas and I'll draft a post for you!")
        return "No text content"

    draft = get_draft(user_id)

    if draft:
        lower = text.lower().strip()

        if lower == "post":
            try:
                post_to_channel(draft["text"])
                clear_draft(user_id)
                send_telegram_message(chat_id, "Posted to your channel!")
                return "Draft published"
            except Exception as e:
                logger.error(f"Error posting: {e}", exc_info=True)
                send_telegram_message(chat_id, f"Failed to post: {str(e)[:200]}")
                return f"Post error: {e}"

        elif lower == "reject":
            clear_draft(user_id)
            send_telegram_message(chat_id, "Draft discarded. Send me new ideas whenever you're ready.")
            return "Draft rejected"

        else:
            try:
                send_telegram_message(chat_id, "Revising your draft...")
                add_feedback(text)
                revised = revise_blog_post(draft["text"], text)
                save_draft(user_id, {
                    "text": revised,
                    "original": draft.get("original", draft["text"]),
                    "revision": draft.get("revision", 0) + 1,
                })
                revision_num = draft.get("revision", 0) + 1
                send_telegram_message(
                    chat_id,
                    f"*Draft v{revision_num + 1}:*\n\n{revised}\n\n"
                    "---\n"
                    "`post` \u2014 publish  |  `reject` \u2014 discard\n"
                    "Or reply with more feedback to revise again",
                )
                return f"Draft revised (v{revision_num + 1})"
            except Exception as e:
                logger.error(f"Error revising: {e}", exc_info=True)
                send_telegram_message(chat_id, f"Something went wrong: {str(e)[:200]}")
                return f"Revise error: {e}"

    try:
        send_telegram_message(chat_id, "Drafting your post...")
        blog_post = generate_blog_post(text)
        save_draft(user_id, {"text": blog_post, "original": text, "revision": 0})
        send_telegram_message(
            chat_id,
            f"*Draft:*\n\n{blog_post}\n\n"
            "---\n"
            "`post` \u2014 publish  |  `reject` \u2014 discard\n"
            "Or reply with feedback to revise",
        )
        return "Draft generated"
    except Exception as e:
        logger.error(f"Error generating: {e}", exc_info=True)
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
            json.dumps({"status": "alive", "bot": "blog-bot", "version": "2.0"}).encode()
        )
"""
Blog Bot — Telegram Webhook Handler (Vercel Serverless Function)

Receives text messages from Telegram, generates a blog post with Claude,
and publishes to your Telegram channel.
"""

import os
import json
import logging
# v1.1 - text-to-blog
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
