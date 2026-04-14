"""Postr AI — Telegram webhook (multi-tenant). Per-platform draft messages + images + scheduling."""
import json
import os
import time
import traceback
from http.server import BaseHTTPRequestHandler

from api._lib import db, ai, telegram, linkedin, x as xlib, website
from api._lib.crypto import decrypt, encrypt

VERSION = "postr-ai-1.4.2"
BOT_USERNAME = "PostrAIBot"

PLATFORMS = ("linkedin", "x", "tg", "blog")
LABEL = {"linkedin": "LinkedIn", "x": "X", "tg": "Telegram channel", "blog": "Website"}
EMOJI = {"linkedin": "🔗", "x": "🐦", "tg": "📣", "blog": "🌐"}

# Public base URL for the blog API (used to show users how to embed their feed).
# Falls back to Vercel's auto-set VERCEL_URL, then to the known production host.
_VERCEL_URL = os.environ.get("VERCEL_URL", "").strip()
PUBLIC_BASE_URL = (
    os.environ.get("PUBLIC_BASE_URL", "").strip()
    or (f"https://{_VERCEL_URL}" if _VERCEL_URL else "")
    or "https://voice-blog-bot.vercel.app"
).rstrip("/")


def _website_setup_text(tg_id: int) -> str:
    """Markdown-formatted instructions for embedding the blog API on any site."""
    base = PUBLIC_BASE_URL
    feed = f"{base}/api/blog/posts/{tg_id}"
    single = f"{base}/api/blog/post/{tg_id}/{{slug}}"
    snippet = (
        "<div id=\"postr-blog\"></div>\n"
        "<script>\n"
        f"fetch('{feed}')\n"
        "  .then(r => r.json())\n"
        "  .then(d => {\n"
        "    document.getElementById('postr-blog').innerHTML =\n"
        "      d.posts.map(p =>\n"
        "        `<article>\n"
        "           <h2>${p.title}</h2>\n"
        "           <small>${new Date(p.published_at*1000).toLocaleDateString()}</small>\n"
        "           <div>${p.content.replace(/\\n/g,'<br>')}</div>\n"
        "         </article>`\n"
        "      ).join('');\n"
        "  });\n"
        "</script>"
    )
    return (
        "🌐 *Your Website Blog API*\n\n"
        "Postr hosts your posts and exposes them via a public JSON API. "
        "Add the snippet below to *any* website (WordPress, Webflow, plain HTML, Next.js, etc.) "
        "to display them — platform-agnostic.\n\n"
        "*Your personal feed (list):*\n"
        f"`{feed}`\n\n"
        "*Single post URL:*\n"
        f"`{single}`\n\n"
        "✅ CORS is enabled (`Access-Control-Allow-Origin: *`) so browsers on any domain can fetch directly.\n\n"
        "*Copy-paste snippet for your site:*\n"
        f"```html\n{snippet}\n```\n\n"
        "*Query params for the list endpoint:*\n"
        "• `limit` — default 10, max 50\n"
        "• `offset` — default 0\n\n"
        "*Response shape:*\n"
        "`{posts: [{id, title, slug, content, tags, image_url, published_at, updated_at}], total, limit, offset}`\n\n"
        f"Open your feed in a browser to test: {feed}"
    )


def cmd_website(chat_id, tg_id):
    telegram.send_message(
        chat_id,
        _website_setup_text(tg_id),
        parse_mode="Markdown",
        reply_markup=telegram.inline_kb([[("🔗 Open my feed", f"{PUBLIC_BASE_URL}/api/blog/posts/{tg_id}")]]),
    )


def _blog_preview_text(blog: dict) -> str:
    """Render a blog dict {title, content, tags} as a human-readable preview."""
    if not isinstance(blog, dict):
        blog = {}
    title = (blog.get("title") or "Untitled").strip()
    content = (blog.get("content") or "").strip()
    tags = blog.get("tags") or []
    preview = content if len(content) <= 1200 else content[:1197].rstrip() + "…"
    parts = [f"📝 {title}", "", preview]
    if tags:
        parts.append("")
        parts.append("Tags: " + ", ".join(f"#{t}" for t in tags))
    return "\n".join(parts)


def connect_keyboard(user: dict) -> dict:
    rows = []
    if not user.get("li_token"):
        rows.append([("🔗 Connect LinkedIn", "cb:connect:linkedin")])
    if not user.get("x_access"):
        rows.append([("🐦 Connect X", "cb:connect:x")])
    if not user.get("tg_channel_id") and (user.get("li_token") or user.get("x_access")):
        rows.append([("📣 Connect Telegram channel", "cb:connect:telegram")])
    # Website API: always show — no OAuth, just instructions to embed on their site.
    rows.append([("🌐 Website blog API", "cb:connect:website")])
    if len(rows) == 1 and rows[0][0][1] == "cb:connect:website":
        # Only website left to show — means all OAuth channels connected.
        rows.insert(0, [("✓ All accounts connected — send me any text", "cb:noop")])
    return telegram.inline_kb(rows)


STRIPE_LINK = os.environ.get("STRIPE_PAYMENT_LINK", "")
ADMIN_TG_IDS = set(filter(None, os.environ.get("ADMIN_TG_IDS", "").split(",")))


WHATS_NEW_TEXT = (
    "🚀 *Here's everything we've added to Postr AI since the first launch:*\n\n"
    "🎙️ Started with voice-message → AI draft → one-tap publish to *LinkedIn* and *X (Twitter)*.\n\n"
    "Since then:\n\n"
    "📢 *Telegram channel publishing* — post to your own channel alongside LinkedIn and X.\n\n"
    "🖼️ *Image support* — attach a photo and it goes out to every platform.\n\n"
    "⏰ *Scheduling* — queue posts for later instead of publishing immediately.\n\n"
    "💳 *Pro tier via Stripe* — upgrade for higher limits and premium features.\n\n"
    "🌍 *Full emoji & UTF-8 support* — your posts render correctly everywhere.\n\n"
    "🌐 *Blog Publishing API* — every post is now also hosted as a JSON feed, so you own your content.\n\n"
    "🛠️ *New /website command* — get a feed URL + copy-paste embed snippet to display your posts on any site (WordPress, Webflow, custom HTML). Platform-agnostic, CORS-enabled.\n\n"
    "More shipping soon. 🔥"
)


def cmd_announce(chat_id: int, tg_id: int) -> None:
    """Admin-only: broadcast the WHATS_NEW_TEXT to every user."""
    if str(tg_id) not in ADMIN_TG_IDS:
        telegram.send_message(chat_id, "⛔ Admin only.")
        return
    telegram.send_message(chat_id, "📣 Starting broadcast…")
    try:
        user_ids = db.scan_user_ids()
    except Exception as e:
        telegram.send_message(chat_id, f"❌ Failed to scan users: {e}")
        return
    sent = 0
    blocked = 0
    errors = 0
    for uid in user_ids:
        try:
            res = telegram.send_message(uid, WHATS_NEW_TEXT, parse_mode="Markdown")
            if res.get("ok"):
                sent += 1
            else:
                err = str(res.get("error", "")).lower()
                if any(k in err for k in ("blocked", "chat not found", "deactivated", "kicked")):
                    blocked += 1
                else:
                    errors += 1
        except Exception:
            errors += 1
        time.sleep(0.05)  # ~20/sec — under Telegram's 30/sec bot broadcast limit
    telegram.send_message(
        chat_id,
        f"✅ Broadcast done.\nTotal users: {len(user_ids)}\nSent: {sent}\nBlocked/inactive: {blocked}\nErrors: {errors}",
    )


def cmd_stats(chat_id: int, tg_id: int) -> None:
    """Admin-only: total users + DAU/WAU."""
    if str(tg_id) not in ADMIN_TG_IDS:
        telegram.send_message(chat_id, "⛔ Admin only.")
        return
    try:
        ids = db.scan_user_ids()
    except Exception as e:
        telegram.send_message(chat_id, f"❌ Failed: {e}")
        return
    now = int(time.time())
    dau = wau = mau = 0
    for uid in ids:
        u = db.get_user(uid)
        ls = int(u.get("last_seen", 0) or 0)
        if not ls:
            continue
        delta = now - ls
        if delta <= 86400:
            dau += 1
        if delta <= 7 * 86400:
            wau += 1
        if delta <= 30 * 86400:
            mau += 1
    telegram.send_message(
        chat_id,
        (
            f"📊 Postr AI stats\n\n"
            f"Total users: {len(ids)}\n"
            f"DAU (24h): {dau}\n"
            f"WAU (7d): {wau}\n"
            f"MAU (30d): {mau}\n\n"
            f"Note: last_seen is recorded from v1.4.2 onward — older users only show up once they interact again."
        ),
    )


def platform_keyboard(platform: str) -> dict:
    return telegram.inline_kb([
        [(f"📤 Post to {LABEL[platform]}", f"cb:post:{platform}"), (f"⏰ Schedule", f"cb:sched:{platform}")],
        [("✨ AI rewrite", f"cb:ai:{platform}"), ("✕ Cancel", f"cb:cancel:{platform}")],
    ])


def format_platform_message(platform: str, text, has_image: bool = False) -> str:
    img = " 🖼" if has_image else ""
    # Blog drafts are dicts; render them via _blog_preview_text.
    if platform == "blog":
        body = _blog_preview_text(text if isinstance(text, dict) else {"content": str(text or "")})
    else:
        body = str(text or "")
    return f"━━━ {EMOJI[platform]} {LABEL[platform]}{img} ━━━\n\n{body}"


def ensure_x_token(user: dict):
    access_enc = user.get("x_access")
    if not access_enc:
        return None
    if user.get("x_expires_at", 0) - 60 > int(time.time()):
        return decrypt(access_enc)
    refresh_enc = user.get("x_refresh")
    if not refresh_enc:
        return decrypt(access_enc)
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


def cmd_start(chat_id, tg_id, first_name=""):
    user = db.update_user(tg_id, first_name=first_name)
    name = first_name or "there"
    text = (
        f"👋 Hey {name}, I'm Postr AI.\n\n"
        "Send me any text (or a photo with a caption) and I'll prep a separate draft for each connected platform "
        "(LinkedIn, X, Telegram channel, your website). For each one you can post it, AI-rewrite it, or cancel independently.\n\n"
        "Your website publishes via a platform-agnostic blog API — you get a personal feed URL + a snippet "
        "you paste into any site (WordPress, Webflow, plain HTML, etc.). Tap 🌐 Website below to see it, or run /website anytime.\n\n"
        "First, connect your accounts:"
    )
    telegram.send_message(chat_id, text, reply_markup=connect_keyboard(user))


def cmd_status(chat_id, tg_id):
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


def cmd_disconnect(chat_id, tg_id):
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
    rows.append([("Cancel", "cb:cancel:none")])
    telegram.send_message(chat_id, "What do you want to disconnect?", reply_markup=telegram.inline_kb(rows))


def cmd_setchannel(chat_id, tg_id, arg):
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


def handle_text(chat_id, tg_id, text, message_id, image_file_id: str = ""):
    user = db.get_user(tg_id)
    if not user:
        return cmd_start(chat_id, tg_id)
    has_li = bool(user.get("li_token"))
    has_x = bool(user.get("x_access"))
    has_tg = bool(user.get("tg_channel_id"))
    if not (has_li or has_x or has_tg):
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
    if image_file_id:
        draft["image_file_id"] = image_file_id
    db.save_draft(tg_id, draft)

    enabled = []
    if has_li: enabled.append("linkedin")
    if has_x: enabled.append("x")
    if has_tg: enabled.append("tg")
    # Blog publishing is always available once any channel is connected —
    # it doesn't require an OAuth connection (posts go to our own store).
    enabled.append("blog")
    intro = f"Drafts ready for {len(enabled)} platform(s)"
    if image_file_id:
        intro += " (with image)"
    telegram.send_message(chat_id, intro + ". Review each below:")
    for p in enabled:
        # For blog, pass the dict; for others, pass the string variant.
        payload = draft.get("blog") if p == "blog" else draft.get(p, "")
        telegram.send_message(
            chat_id,
            format_platform_message(p, payload, bool(image_file_id)),
            reply_markup=platform_keyboard(p),
        )


def _post_to_platform(platform, user, draft):
    image_file_id = draft.get("image_file_id", "")
    img_bytes = None
    img_mime = "image/jpeg"
    if image_file_id:
        try:
            img_bytes, img_mime = telegram.fetch_photo_bytes(image_file_id)
        except Exception as e:
            return {"ok": False, "error": f"image fetch failed: {str(e)[:160]}"}

    if platform == "linkedin":
        token = decrypt(user["li_token"])
        asset_urn = None
        if img_bytes:
            try:
                asset_urn = linkedin.upload_image(token, user["li_urn"], img_bytes)
            except Exception as e:
                return {"ok": False, "error": f"LinkedIn image upload failed: {str(e)[:200]}"}
        return linkedin.create_post(token, user["li_urn"], draft.get("linkedin", ""), asset_urn=asset_urn)

    if platform == "x":
        access = ensure_x_token(user)
        if not access:
            return {"ok": False, "error": "token refresh failed"}
        media_id = None
        if img_bytes:
            try:
                media_id = xlib.upload_media(access, img_bytes, img_mime)
            except Exception as e:
                return {"ok": False, "error": f"X media upload failed: {str(e)[:200]}"}
        return xlib.create_tweet(access, draft.get("x", ""), media_id=media_id)

    if platform == "tg":
        tg_text = draft.get("tg", "")
        # Add "posted via @PostrAIBot" footer for free-plan users
        if user.get("plan", "free") == "free":
            footer = "\n\nPosted via @PostrAIBot"
            tg_text = tg_text + footer
        if image_file_id:
            rr = telegram.send_photo(user["tg_channel_id"], image_file_id, caption=tg_text)
        else:
            rr = telegram.send_message(user["tg_channel_id"], tg_text)
        return {"ok": rr.get("ok", False), "error": rr.get("error", "")}

    if platform == "blog":
        blog = draft.get("blog") or {}
        title = (blog.get("title") or "").strip() or "Untitled post"
        content = (blog.get("content") or draft.get("source") or "").strip()
        tags = blog.get("tags") or []
        if not content:
            return {"ok": False, "error": "no blog content to publish"}
        try:
            post = website.publish_post(
                user_id=user["tg_id"],
                title=title,
                content=content,
                image_url=None,  # v1: images-on-blog not yet supported from TG file IDs
                tags=tags,
            )
            return {"ok": True, "result": post, "slug": post["slug"], "path": f"/api/blog/post/{post['user_id']}/{post['slug']}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"website publish failed: {str(e)[:200]}"}

    return {"ok": False, "error": "unknown platform"}


def handle_callback(cb):
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    tg_id = cb["from"]["id"]
    cb_id = cb["id"]

    if data == "cb:noop":
        telegram.answer_callback(cb_id)
        return

    if data.startswith("cb:cancel:"):
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
        elif provider == "website":
            cmd_website(chat_id, tg_id)
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

    if data.startswith("cb:ai:"):
        platform = data.split(":")[2]
        draft = db.get_draft(tg_id)
        if not draft or not draft.get("source"):
            telegram.answer_callback(cb_id, "Nothing to rewrite")
            return
        telegram.answer_callback(cb_id, "AI rewriting...")
        try:
            new_value = ai.rewrite_one(draft["source"], platform)
            draft[platform] = new_value
            db.save_draft(tg_id, draft)
            telegram.edit_message(
                chat_id,
                message_id,
                format_platform_message(platform, new_value, bool(draft.get("image_file_id"))),
                reply_markup=platform_keyboard(platform),
            )
        except Exception as e:
            telegram.send_message(chat_id, f"AI rewrite failed: {str(e)[:200]}")
        return

    if data.startswith("cb:sched:") and not data.startswith("cb:schedset:"):
        platform = data.split(":")[2]
        draft = db.get_draft(tg_id)
        if not draft:
            telegram.answer_callback(cb_id, "Draft expired")
            return
        telegram.answer_callback(cb_id)
        sched_payload = draft.get("blog") if platform == "blog" else draft.get(platform, "")
        telegram.edit_message(
            chat_id,
            message_id,
            format_platform_message(platform, sched_payload, bool(draft.get("image_file_id")))
            + "\n\n⏰ When should this post go out?",
            reply_markup=telegram.inline_kb([
                [("1 hour", f"cb:schedset:{platform}:60"), ("3 hours", f"cb:schedset:{platform}:180")],
                [("6 hours", f"cb:schedset:{platform}:360"), ("12 hours", f"cb:schedset:{platform}:720")],
                [("Tomorrow 9 AM", f"cb:schedset:{platform}:t9"), ("Cancel", f"cb:cancel:{platform}")],
            ]),
        )
        return

    if data.startswith("cb:schedset:"):
        parts = data.split(":")
        platform = parts[2]
        offset_raw = parts[3]
        draft = db.get_draft(tg_id)
        if not draft:
            telegram.answer_callback(cb_id, "Draft expired")
            return
        user = db.get_user(tg_id)
        if not user:
            telegram.answer_callback(cb_id, "Not signed in")
            return
        now = int(time.time())
        if offset_raw == "t9":
            # Tomorrow 9 AM UTC (user can adjust later if needed)
            tomorrow = now + 86400
            day_start = tomorrow - (tomorrow % 86400)
            run_at = day_start + 9 * 3600
        else:
            run_at = now + int(offset_raw) * 60
        job_id = f"sj:{tg_id}:{platform}:{run_at}"
        payload = {
            "tg_id": tg_id,
            "chat_id": chat_id,
            "platform": platform,
            "text": "" if platform == "blog" else draft.get(platform, ""),
            "image_file_id": draft.get("image_file_id", ""),
        }
        if platform == "blog":
            payload["blog"] = draft.get("blog") or {}
        db.schedule_job(job_id, run_at, payload)
        delta = run_at - now
        if delta >= 3600:
            when = f"{delta // 3600}h {(delta % 3600) // 60}m"
        else:
            when = f"{delta // 60}m"
        telegram.edit_message(
            chat_id,
            message_id,
            f"⏰ Scheduled for {LABEL[platform]} in {when}.",
            reply_markup={"inline_keyboard": []},
        )
        telegram.answer_callback(cb_id, "Scheduled!")
        return

    if data.startswith("cb:post:"):
        platform = data.split(":")[2]
        draft = db.get_draft(tg_id)
        if not draft:
            telegram.answer_callback(cb_id, "Draft expired")
            return
        user = db.get_user(tg_id)
        if not user:
            telegram.answer_callback(cb_id, "Not signed in")
            return
        is_admin = str(tg_id) in ADMIN_TG_IDS
        allowed, used, limit = (True, 0, 999) if is_admin else db.check_and_increment_quota(tg_id)
        if not allowed:
            telegram.answer_callback(cb_id, "Free limit reached")
            upgrade_rows = []
            if STRIPE_LINK:
                upgrade_rows = [[("⚡ Upgrade to Pro", STRIPE_LINK)]]
            telegram.edit_message(
                chat_id,
                message_id,
                f"You've used all {limit} free posts this month.\n\nUpgrade to Pro for unlimited posts!",
                reply_markup=telegram.inline_kb(upgrade_rows) if upgrade_rows else {"inline_keyboard": []},
            )
            return
        try:
            r = _post_to_platform(platform, user, draft)
        except Exception as e:
            r = {"ok": False, "error": str(e)[:200]}
        if r.get("ok"):
            success_text = f"✅ Posted to {LABEL[platform]}"
            extra_kb = None
            if platform == "blog" and r.get("slug"):
                feed_url = f"{PUBLIC_BASE_URL}/api/blog/posts/{tg_id}"
                post_url = f"{PUBLIC_BASE_URL}/api/blog/post/{tg_id}/{r['slug']}"
                success_text += (
                    f"\n\n🌐 Live on your blog feed:\n{feed_url}\n\n"
                    f"This specific post:\n{post_url}\n\n"
                    "Run /website to get the snippet to embed this feed on your site."
                )
                extra_kb = telegram.inline_kb([
                    [("📖 View post JSON", post_url)],
                    [("🛠 Embed setup", "cb:connect:website")],
                ])
            telegram.edit_message(
                chat_id,
                message_id,
                success_text,
                reply_markup=extra_kb or {"inline_keyboard": []},
            )
            telegram.answer_callback(cb_id, "Posted")
        else:
            retry_payload = draft.get("blog") if platform == "blog" else draft.get(platform, "")
            telegram.edit_message(
                chat_id,
                message_id,
                format_platform_message(platform, retry_payload, bool(draft.get("image_file_id"))) + f"\n\n❌ {r.get('error','unknown error')}",
                reply_markup=platform_keyboard(platform),
            )
            telegram.answer_callback(cb_id, "Failed")
        return

    telegram.answer_callback(cb_id)


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

    def _handle(self, update):
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
        text = msg.get("text", "") or msg.get("caption", "") or ""
        if not chat_id or not tg_id:
            return

        image_file_id = ""
        photos = msg.get("photo")
        if photos and isinstance(photos, list) and photos:
            image_file_id = photos[-1].get("file_id", "")
        else:
            doc = msg.get("document") or {}
            if doc.get("mime_type", "").startswith("image/"):
                image_file_id = doc.get("file_id", "")

        if text.startswith("/start"):
            cmd_start(chat_id, tg_id, first_name)
        elif text.startswith("/status"):
            cmd_status(chat_id, tg_id)
        elif text.startswith("/disconnect"):
            cmd_disconnect(chat_id, tg_id)
        elif text.startswith("/setchannel"):
            cmd_setchannel(chat_id, tg_id, text[len("/setchannel"):].strip())
        elif text.startswith("/website") or text.startswith("/blog") or text.startswith("/api"):
            cmd_website(chat_id, tg_id)
        elif text.startswith("/announce") or text.startswith("/broadcast"):
            cmd_announce(chat_id, tg_id)
        elif text.startswith("/stats") or text.startswith("/users"):
            cmd_stats(chat_id, tg_id)
        elif text.startswith("/help"):
            telegram.send_message(
                chat_id,
                "Send me any text (or a photo with a caption) and I'll prep a separate draft for each connected platform.\n\n"
                "/start — welcome + connect accounts\n"
                "/status — see your connections and usage\n"
                "/disconnect — disconnect an account\n"
                "/setchannel @name — connect a Telegram channel\n"
                "/website — get your blog API URL + embed snippet for your site",
            )
        elif text.startswith("/"):
            telegram.send_message(chat_id, "Unknown command. Try /help.")
        elif text or image_file_id:
            handle_text(chat_id, tg_id, text, msg.get("message_id", 0), image_file_id=image_file_id)

    def _json(self, code, payload):
        b = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
