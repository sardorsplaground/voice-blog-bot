"""GET / DELETE /api/blog/post/:user_id/:slug — read or delete a single post.

The vercel.json route rewrites this path to ?user_id=...&slug=... so we read
both from the query string, falling back to the path segments for safety.

GET:
  Public. Returns { "post": {...} } or { "error": "not found" } with 404.
  CORS: Access-Control-Allow-Origin: *

DELETE:
  Requires `x-api-key` header matching the BLOG_API_KEY env var.
  Returns { "ok": true } or 404 if the post doesn't exist.
"""
import os
import json
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from api._lib import website


BLOG_API_KEY = os.environ.get("BLOG_API_KEY", "")


def _cors_headers(h):
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Access-Control-Allow-Methods", "GET, DELETE, OPTIONS")
    h.send_header("Access-Control-Allow-Headers", "Content-Type, x-api-key, Authorization")


def _json(h, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    _cors_headers(h)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    if status != 204:
        h.wfile.write(body)


def _extract(path: str):
    """Return (user_id_str, slug_str) from query or path."""
    parsed = urlparse(path)
    qs = parse_qs(parsed.query)
    uid = qs.get("user_id", [""])[0]
    slug = qs.get("slug", [""])[0]
    if uid and slug:
        return uid, slug
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return uid, slug


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        uid_s, slug = _extract(self.path)
        try:
            user_id = int(uid_s)
        except (TypeError, ValueError):
            return _json(self, 400, {"error": "invalid user_id"})
        if not slug:
            return _json(self, 400, {"error": "slug is required"})
        try:
            post = website.get_post(user_id, slug)
        except Exception as e:
            traceback.print_exc()
            return _json(self, 500, {"error": f"lookup failed: {str(e)[:200]}"})
        if not post:
            return _json(self, 404, {"error": "not found"})
        return _json(self, 200, {"post": post})

    def do_DELETE(self):
        if not BLOG_API_KEY:
            return _json(self, 503, {"error": "delete disabled: BLOG_API_KEY not configured"})
        key = self.headers.get("x-api-key") or self.headers.get("X-Api-Key")
        if key != BLOG_API_KEY:
            return _json(self, 401, {"error": "unauthorized"})
        uid_s, slug = _extract(self.path)
        try:
            user_id = int(uid_s)
        except (TypeError, ValueError):
            return _json(self, 400, {"error": "invalid user_id"})
        if not slug:
            return _json(self, 400, {"error": "slug is required"})
        try:
            existed = website.delete_post(user_id, slug)
        except Exception as e:
            traceback.print_exc()
            return _json(self, 500, {"error": f"delete failed: {str(e)[:200]}"})
        if not existed:
            return _json(self, 404, {"error": "not found"})
        return _json(self, 200, {"ok": True})
