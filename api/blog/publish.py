"""POST /api/blog/publish — publish a blog post for a user.

Request body (JSON):
{
  "user_id": 12345,               // required (int)
  "title": "My post",             // required
  "content": "# Heading...",      // required, markdown
  "image_url": "https://...",    // optional
  "tags": ["ai", "startups"],    // optional
  "slug": "my-custom-slug"       // optional (auto-derived from title)
}

Auth: requires `x-api-key` header matching BLOG_API_KEY env var.
If BLOG_API_KEY is not configured the endpoint returns 503.

Response 200:
{
  "ok": true,
  "post": { ... },
  "path": "/api/blog/post/{user_id}/{slug}"
}
"""
import os
import json
import traceback
from http.server import BaseHTTPRequestHandler

from api._lib import website


BLOG_API_KEY = os.environ.get("BLOG_API_KEY", "")


def _cors_headers(h):
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
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


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if not BLOG_API_KEY:
            return _json(self, 503, {"error": "publish disabled: BLOG_API_KEY not configured"})
        key = self.headers.get("x-api-key") or self.headers.get("X-Api-Key")
        if key != BLOG_API_KEY:
            return _json(self, 401, {"error": "unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError) as e:
            return _json(self, 400, {"error": f"invalid JSON body: {e}"})

        try:
            post = website.publish_post(
                user_id=data.get("user_id"),
                title=data.get("title", ""),
                content=data.get("content", ""),
                image_url=data.get("image_url") or None,
                tags=data.get("tags") or [],
                slug=data.get("slug") or None,
            )
        except ValueError as e:
            return _json(self, 400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return _json(self, 500, {"error": f"publish failed: {str(e)[:200]}"})

        return _json(self, 200, {
            "ok": True,
            "post": post,
            "path": f"/api/blog/post/{post['user_id']}/{post['slug']}",
        })
