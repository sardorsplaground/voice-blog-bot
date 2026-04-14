"""Cron handler — fires scheduled posts. Called by Vercel cron every minute."""
import json
import time
import traceback
from http.server import BaseHTTPRequestHandler

from api._lib import db, telegram, linkedin, x as xlib, website
from api._lib.crypto import decrypt, encrypt


def _ensure_x_token(user: dict):
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


def _post_job(job: dict) -> dict:
    """Execute a single scheduled job. Returns {ok, error?}."""
    tg_id = job["tg_id"]
    platform = job["platform"]
    text = job.get("text", "")
    image_file_id = job.get("image_file_id", "")

    user = db.get_user(tg_id)
    if not user:
        return {"ok": False, "error": "user not found"}

    img_bytes = None
    img_mime = "image/jpeg"
    if image_file_id:
        try:
            img_bytes, img_mime = telegram.fetch_photo_bytes(image_file_id)
        except Exception as e:
            return {"ok": False, "error": f"image fetch: {str(e)[:160]}"}

    if platform == "linkedin":
        token = decrypt(user.get("li_token", ""))
        if not token:
            return {"ok": False, "error": "LinkedIn not connected"}
        asset_urn = None
        if img_bytes:
            try:
                asset_urn = linkedin.upload_image(token, user["li_urn"], img_bytes)
            except Exception as e:
                return {"ok": False, "error": f"LI image: {str(e)[:200]}"}
        return linkedin.create_post(token, user["li_urn"], text, asset_urn=asset_urn)

    if platform == "x":
        access = _ensure_x_token(user)
        if not access:
            return {"ok": False, "error": "X token refresh failed"}
        media_id = None
        if img_bytes:
            try:
                media_id = xlib.upload_media(access, img_bytes, img_mime)
            except Exception as e:
                return {"ok": False, "error": f"X media: {str(e)[:200]}"}
        return xlib.create_tweet(access, text, media_id=media_id)

    if platform == "tg":
        ch = user.get("tg_channel_id")
        if not ch:
            return {"ok": False, "error": "TG channel not connected"}
        if image_file_id:
            rr = telegram.send_photo(ch, image_file_id, caption=text)
        else:
            rr = telegram.send_message(ch, text)
        return {"ok": rr.get("ok", False), "error": rr.get("error", "")}

    if platform == "blog":
        blog = job.get("blog") or {}
        title = (blog.get("title") or "").strip() or "Untitled post"
        content = (blog.get("content") or "").strip()
        tags = blog.get("tags") or []
        if not content:
            return {"ok": False, "error": "no blog content"}
        try:
            post = website.publish_post(
                user_id=user["tg_id"],
                title=title,
                content=content,
                image_url=None,
                tags=tags,
            )
            return {"ok": True, "result": post, "path": f"/api/blog/post/{post['user_id']}/{post['slug']}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"blog publish: {str(e)[:200]}"}

    return {"ok": False, "error": "unknown platform"}


LABEL = {"linkedin": "LinkedIn", "x": "X", "tg": "Telegram channel", "blog": "Website"}


def run_due_jobs():
    """Process all due scheduled jobs."""
    jobs = db.get_due_jobs()
    results = []
    for job_id, payload in jobs:
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "?")
        try:
            r = _post_job(payload)
        except Exception as e:
            r = {"ok": False, "error": str(e)[:200]}
        # Notify user in Telegram
        if chat_id:
            label = LABEL.get(platform, platform)
            if r.get("ok"):
                telegram.send_message(chat_id, f"⏰✅ Scheduled post sent to {label}!")
            else:
                telegram.send_message(chat_id, f"⏰❌ Scheduled post to {label} failed: {r.get('error', 'unknown')[:200]}")
        db.remove_job(job_id)
        results.append({"job_id": job_id, "ok": r.get("ok")})
    return results


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            results = run_due_jobs()
            b = json.dumps({"ok": True, "processed": len(results), "results": results}).encode()
        except Exception:
            traceback.print_exc()
            b = json.dumps({"ok": False, "error": traceback.format_exc()[:500]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        self.do_GET()
