"""Postr AI — Telegram webhook (multi-tenant).

Routes Vercel POST /api/webhook to this handler.
Flow:
  /start            -> welcome + connect buttons
  /status           -> show which accounts are connected + usage
  /disconnect       -> menu to disconnect LinkedIn or X
  any plain text    -> AI generates LinkedIn + X variants -> inline buttons
  callback queries  -> post to LI / X / both / regenerate / cancel
"""
import json
import time
import traceback
from http.server import BaseHTTPRequestHandler

from api._lib import db, ai, telegram, linkedin, x as xlib
from api._lib.crypto import decrypt, encrypt

VERSION = "postr-ai-1.0.0"
BOT_USERNAME = "PostrAIBot"


# ---------- helpers ----------

def connect_keyboard(user: dict) -> dict:
    rows = []
    if not user.get("li_token"):
        rows.append([("🔗 Connect LinkedIn", "cb:connect:linkedin")])
    if not user.get("x_access"):
        rows.append([("🐦 Connect X", "cb:connect:x")])
    if not user.get("tg_channel_id") and (user.get("li_token") or user.get("x_access")):
        rows.append([("📣 Connect Telegram channel", "cb:connect:telegram")])
    if not rows:
        rows = [[("✓ All connected — send me any text", "cb:noop")]]
    return telegram.inline_kb(rows)


def draft_keyboard(has_li: bool, has_x: bool) -> dict:
    rows = []
    if has_li and has_x:
        rows.append([("📤 Post to both", "cb:post:both")])
    if has_li:
        rows.append([("LinkedIn", "cb:post:linkedin")])
    if has_x:
        rows.append([("X", "cb:post:x")])
    rows.append([("✨ AI rewrite", "cb:airewrite"), ("✕ Cancel", "cb:cancel")])
    return telegram.inline_kb(rows)


def format_draft(draft: dict) -> str:
    li = draft.get("linkedin", "")
    x = draft.get("x", "")
    return (
        "Here's what I'd post:\n\n"
        "━━━ LinkedIn ━━━\n"
        f"{li}\n\n"
        "━━━ X ━━━\n"
        f"{x}\n\n"
        "Pick where to post:"
    )


def ensure_x_token(user: dict) -> str | None:
    """Refresh X access token if expired. Returns valid access token or None."""
    access_enc = user.get("x_access")
    if not access_enc:
        return None
    if user.get("x_expires_at", 0) - 60 > int(time.time()):
        return decrypt(access_enc)
    refresh_enc = user.get("x_refresh")
    if not refresh_enc:
        return decrypt(access_enc)  # try anyway
    try:
        tok = xlib.refresh_access(decrypt(refresh_enc))
        new_access = tok["access_token"]
        new_refresh = tok.get("refresh_token", decrypt(refresh_enc))
        db.update_user(
            user["tg_id"],
            x_access=encrypt(new_access),
            x_refresh=encrypt(new_refresh),
            x_expires_at=int(time.time()) + int(tok.get("expires_in", 7200)),
        )
        return new_access
    except Exception:
        return None


# ---------- command handlers ----------

def cmd_start(chat_id: int, tg_id: int, first_name: str = ""):
    user = db.update_user(tg_id, first_name=first_name)
    name = first_name or "there"
    text = (
        f"👋 Hey {name}, I'm Postr AI.\n\n"
        "Send me any thought, draft, or rough idea — I'll turn it into a polished LinkedIn post and a sharp X post, "
        "then post both with one tap.\n\n"
        "First, connect your accounts:"
    )
    telegram.send_message(chat_id, text, reply_markup=connect_keyboard(user))


def cmd_status(chat_id: int, tg_id: int):
    user = db.get_user(tg_id) or {}
    li = "✓ " + user.get("li_name", "connected") if user.get("li_token") else "— not connected"
    x = "✓ @" + user.get("x_username", "") if user.get("x_access") else "— not connected"
    ch = "✓ " + user.get("tg_channel_name", "") if user.get("tg_channel_id") else "— not connected"
    used = user.get("posts_used", 0)
    plan = user.get("plan", "free")
    limit = db.FREE_LIMIT if plan == "free" else "∞"
    text = (
        f"LinkedIn: {li}\n"
        f"X: {x}\n"
        f"Telegram channel: {ch}\n\n"
        f"Plan: {plan} ({used}/{limit} posts this month)"
    )
    telegram.send_message(chat_id, text, reply_markup=connect_keyboard(user))


def cmd_disconnect(chat_id: int, tg_id: int):
    user = db.get_user(tg_id) or {}
    rows = []
    if user.get("li_token"):
        rows.append([("Disconnect LinkedIn", "cb:disc:linkedin")])
    if user.get("x_access"):
        rows.append([("Disconnect X", "cb:disc:x")])
    if user.get("tg_channel_id"):
        rows.append([("Disconnect Telegram channel", "cb:disc:telegram")])
    if not rows:
        telegram.send_message(chat_id, "Nothing to disconnect.")
        return
    rows.append([("Cancel", "cb:cancel")])
    telegram.send_message(chat_id, "What do you want to disconnect?", reply_markup=telegram.inline_kb(rows))


def cmd_setchannel(chat_id: int, tg_id: int, arg: str):
    arg = (arg or "").strip()
    if not arg:
        telegram.send_message(chat_id, "Usage: /setchannel @yourchannel")
        return
    if not arg.startswith("@") and not arg.lstrip("-").isdigit():
        arg = "@" + arg
    chat = telegram.get_chat(arg)
    if not chat.get("ok"):
        telegram.send_message(chat_id, f"Couldn't find that channel. Make sure I'm an admin there.\n{chat.get('error','')[:200]}")
        return
    info = chat["result"]
    ch_id = info["id"]
    ch_title = info.get("title") or info.get("username") or str(ch_id)
    me = telegram.get_me()
    if not me.get("ok"):
        telegram.send_message(chat_id, "Couldn't verify bot identity.")
        return
    bot_id = me["result"]["id"]
    member = telegram.get_chat_member(ch_id, bot_id)
    if not member.get("ok") or member["result"].get("status") not in ("administrator", "creator"):
        telegram.send_message(chat_id, "I'm not an admin in that channel. Add me as an admin with 'Post messages' permission, then retry.")
        return
    db.update_user(tg_id, tg_channel_id=ch_id, tg_channel_name=ch_title)
    telegram.send_message(chat_id, f"✅ Connected channel: {ch_title}")


def handle_text(chat_id: int, tg_id: int, text: str, message_id: int):
    user = db.get_user(tg_id)
    if not user:
        return cmd_start(chat_id, tg_id)
    if not user.get("li_token") and not user.get("x_access"):
        telegram.send_message(
            chat_id,
            "You need to connect at least one account first.",
            reply_markup=connect_keyboard(user),
        )
        return
    if len(text) > 4000:
        telegram.send_message(chat_id, "That's too long — keep it under 4000 characters.")
        return
    draft = ai.format_variants(text)
    draft["source"] = text
    db.save_draft(tg_id, draft)
    telegram.send_message(
        chat_id,
        format_draft(draft),
        reply_markup=draft_keyboard(bool(user.get("li_token")), bool(user.get("x_access"))),
    )


# ---------- callback handlers ----------

def handle_callback(cb: dict):
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    tg_id = cb["from"]["id"]
    cb_id = cb["id"]

    if data == "cb:noop":
        telegram.answer_callback(cb_id)
        return

    if data == "cb:cancel":
        db.clear_draft(tg_id)
        telegram.edit_message(chat_id, message_id, "Cancelled.", reply_markup={"inline_keyboard": []})
        telegram.answer_callback(cb_id, "Cancelled")
        return

    if data.startswith("cb:connect:"):
        provider = data.split(":")[2]
        if provider == "linkedin":
            state = db.make_oauth_state(tg_id, "linkedin")
            url = linkedin.authorize_url(state)
            telegram.send_message(
                chat_id,
                "Tap to connect LinkedIn (opens in your browser):",
                reply_markup=telegram.inline_kb([[("Connect LinkedIn", url)]]),
            )
        elif provider == "telegram":
            telegram.send_message(
                chat_id,
                "To connect a Telegram channel:\n\n"
                "1. Add @PostrAIBot as an admin to your channel (with 'Post messages' permission)\n"
                "2. Send me: /setchannel @yourchannel",
            )
        elif provider == "x":
            verifier, challenge = xlib.gen_pkce()
            state = db.make_oauth_state(tg_id, "x", verifier=verifier)
            url = xlib.authorize_url(state, challenge)
            telegram.send_message(
                chat_id,
                "Tap to connect X (opens in your browser):",
                reply_markup=telegram.inline_kb([[("Connect X", url)]]),
            )
        telegram.answer_callback(cb_id)
        return

    if data.startswith("cb:disc:"):
        provider = data.split(":")[2]
        if provider == "linkedin":
            db.update_user(tg_id, li_token="", li_urn="", li_name="", li_expires_at=0)
            telegram.edit_message(chat_id, message_id, "LinkedIn disconnected.", reply_markup={"inline_keyboard": []})
        elif provider == "x":
            db.update_user(tg_id, x_access="", x_refresh="", x_user_id="", x_username="", x_expires_at=0)
            telegram.edit_message(chat_id, message_id, "X disconnected.", reply_markup={"inline_keyboard": []})
        elif provider == "telegram":
            db.update_user(tg_id, tg_channel_id="", tg_channel_name="")
            telegram.edit_message(chat_id, message_id, "Telegram channel disconnected.", reply_markup={"inline_keyboard": []})
        telegram.answer_callback(cb_id)
        return

    if data in ("cb:regen", "cb:airewrite"):
        draft = db.get_draft(tg_id)
        if not draft or not draft.get("source"):
            telegram.answer_callback(cb_id, "Nothing to rewrite")
            return
        telegram.answer_callback(cb_id, "AI rewriting...")
        try:
            new_draft = ai.generate_variants(draft["source"])
            new_draft["source"] = draft["source"]
            db.save_draft(tg_id, new_draft)
            user = db.get_user(tg_id)
            telegram.edit_message(
                chat_id,
                message_id,
                format_draft(new_draft),
                reply_markup=draft_keyboard(bool(user.get("li_token")), bool(user.get("x_access"))),
            )
        except Exception as e:
            telegram.send_message(chat_id, f"Regeneration failed: {str(e)[:200]}")
        return

    if data.startswith("cb:post:"):
        target = data.split(":")[2]  # linkedin | x | both
        draft = db.get_draft(tg_id)
        if not draft:
            telegram.answer_callback(cb_id, "Draft expired")
            return
        user = db.get_user(tg_id)
        if not user:
            telegram.answer_callback(cb_id, "Not signed in")
            return
        allowed, used, limit = db.check_and_increment_quota(tg_id)
        if not allowed:
            telegram.answer_callback(cb_id, "Free limit reached")
            telegram.edit_message(
                chat_id,
                message_id,
                f"You've used all {limit} free posts this month. Upgrade coming soon — for now, reply with 'upgrade' and I'll add you to the early-access list.",
                reply_markup={"inline_keyboard": []},
            )
            return
        results = []
        if target in ("linkedin", "both") and user.get("li_token"):
            try:
                token = decrypt(user["li_token"])
                r = linkedin.create_post(token, user["li_urn"], draft["linkedin"])
                results.append(("LinkedIn", r))
            except Exception as e:
                results.append(("LinkedIn", {"ok": False, "error": str(e)[:200]}))
        if target in ("x", "both") and user.get("x_access"):
            access = ensure_x_token(db.get_user(tg_id))
            if not access:
                results.append(("X", {"ok": False, "error": "token refresh failed"}))
            else:
                r = xlib.create_tweet(access, draft["x"])
                results.append(("X", r))
        if user.get("tg_channel_id") and target in ("linkedin", "both"):
            try:
                rr = telegram.send_message(user["tg_channel_id"], draft.get("linkedin") or draft.get("x") or "")
                results.append(("Telegram channel", {"ok": rr.get("ok", False), "error": rr.get("error", "")}))
            except Exception as e:
                results.append(("Telegram channel", {"ok": False, "error": str(e)[:200]}))
        lines = []
        for name, r in results:
            if r.get("ok"):
                lines.append(f"✅ Posted to {name}")
            else:
                lines.append(f"❌ {name}: {r.get('error', 'unknown error')}")
        db.clear_draft(tg_id)
        telegram.edit_message(chat_id, message_id, "\n".join(lines), reply_markup={"inline_keyboard": []})
        telegram.answer_callback(cb_id, "Done")
        return

    telegram.answer_callback(cb_id)


# ---------- HTTP handler ----------

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._json(200, {"status": "alive", "bot": "Postr AI", "version": VERSION})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            update = json.loads(body)
            self._handle(update)
            self._json(200, {"ok": True})
        except Exception:
            traceback.print_exc()
            self._json(200, {"ok": True})

    def _handle(self, update: dict):
        if "callback_query" in update:
            handle_callback(update["callback_query"])
            return
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        tg_id = msg.get("from", {}).get("id")
        first_name = msg.get("from", {}).get("first_name", "")
        text = msg.get("text", "")
        if not chat_id or not tg_id:
            return
        if text.startswith("/start"):
            cmd_start(chat_id, tg_id, first_name)
        elif text.startswith("/status"):
            cmd_status(chat_id, tg_id)
        elif text.startswith("/disconnect"):
            cmd_disconnect(chat_id, tg_id)
        elif text.startswith("/setchannel"):
            cmd_setchannel(chat_id, tg_id, text[len("/setchannel"):].strip())
        elif text.startswith("/help"):
            telegram.send_message(
                chat_id,
                "Send me any text and I'll turn it into a LinkedIn post and an X post.\n\n"
                "/start — welcome + connect accounts\n"
                "/status — see your connections and usage\n"
                "/disconnect — disconnect an account",
            )
        elif text.startswith("/"):
            telegram.send_message(chat_id, "Unknown command. Try /help.")
        elif text:
            handle_text(chat_id, tg_id, text, msg.get("message_id", 0))

    def _json(self, code: int, payload: dict):
        b = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
