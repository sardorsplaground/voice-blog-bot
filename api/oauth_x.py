"""X OAuth 2.0 PKCE callback. Vercel routes /api/oauth/x/callback here."""
import time
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler

from api._lib import db, x as xlib, telegram
from api._lib.crypto import encrypt


HTML_OK = """<!doctype html><meta charset=utf-8><title>Postr AI — Connected</title>
<style>body{{font-family:-apple-system,Inter,sans-serif;background:#0F172A;color:#fff;display:grid;place-items:center;height:100vh;margin:0}}
.card{{background:#1E293B;padding:48px;border-radius:24px;text-align:center;max-width:420px}}
h1{{margin:0 0 12px;font-size:28px}}p{{color:#94A3B8;margin:0 0 24px}}
a{{display:inline-block;background:linear-gradient(135deg,#6366F1,#8B5CF6);color:#fff;padding:14px 28px;border-radius:12px;text-decoration:none;font-weight:600}}</style>
<div class=card><h1>X connected ✓</h1><p>Head back to Telegram and start sending text — Postr AI will turn it into posts for you.</p>
<a href="https://t.me/PostrAIBot">Open Postr AI</a></div>"""

HTML_ERR = """<!doctype html><meta charset=utf-8><title>Postr AI — Error</title>
<style>body{{font-family:-apple-system,Inter,sans-serif;background:#0F172A;color:#fff;display:grid;place-items:center;height:100vh;margin:0}}
.card{{background:#1E293B;padding:48px;border-radius:24px;text-align:center;max-width:420px}}</style>
<div class=card><h1>Couldn't connect X</h1><p>{msg}</p></div>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = dict(urllib.parse.parse_qsl(qs))
        code = params.get("code")
        state = params.get("state")
        err = params.get("error_description") or params.get("error")
        if err or not code or not state:
            return self._html(400, HTML_ERR.format(msg=err or "Missing code/state"))
        st = db.consume_oauth_state(state)
        if not st or st.get("provider") != "x":
            return self._html(400, HTML_ERR.format(msg="Invalid or expired state"))
        verifier = st.get("verifier")
        if not verifier:
            return self._html(400, HTML_ERR.format(msg="Missing PKCE verifier"))
        try:
            tok = xlib.exchange_code(code, verifier)
            access = tok["access_token"]
            refresh = tok.get("refresh_token", "")
            expires_at = int(time.time()) + int(tok.get("expires_in", 7200))
            me = xlib.get_me(access)
            x_user_id = me["data"]["id"]
            x_username = me["data"].get("username", "")
        except Exception as e:
            return self._html(500, HTML_ERR.format(msg=str(e)[:200]))
        tg_id = st["tg_id"]
        db.update_user(
            tg_id,
            x_access=encrypt(access),
            x_refresh=encrypt(refresh) if refresh else "",
            x_expires_at=expires_at,
            x_user_id=x_user_id,
            x_username=x_username,
        )
        try:
            telegram.send_message(tg_id, "✅ X connected. Send me any text and I'll turn it into posts.")
        except Exception:
            pass
        self._html(200, HTML_OK)

    def _html(self, code: int, body: str):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
