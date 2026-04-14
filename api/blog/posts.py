"""GET /api/blog/posts/:user_id — list a user's blog posts, paginated.

The vercel.json route rewrites /api/blog/posts/:user_id to this handler with
?user_id=... so we just read from the query string.

Query params:
    limit  (int, default 10, max 50)
    offset (int, default 0)

Response 200:
{
  "posts": [ { id, user_id, title, slug, content, image_url, tags, published_at, updated_at }, ... ],
  "total": <int>,
  "limit": <int>,
  "offset": <int>
}

CORS: Access-Control-Allow-Origin: *
"""
import json
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from api._lib import website


def _cors_headers(h):
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    h.send_header("Access-Control-Allow-Headers", "Content-Type")


def _json(h, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    _cors_headers(h)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    if status != 204:
        h.wfile.write(body)


def _extract_user_id(path: str, qs: dict) -> str:
    # Prefer ?user_id=... from the rewrite; fall back to the last path segment.
    uid = qs.get("user_id", [""])[0]
    if uid:
        return uid
    parts = [p for p in urlparse(path).path.split("/") if p]
    return parts[-1] if parts else ""


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        uid_s = _extract_user_id(self.path, qs)
        try:
            user_id = int(uid_s)
        except (TypeError, ValueError):
            return _json(self, 400, {"error": "invalid user_id"})
        try:
            limit = int(qs.get("limit", ["10"])[0])
            offset = int(qs.get("offset", ["0"])[0])
        except ValueError:
            return _json(self, 400, {"error": "limit and offset must be integers"})

        try:
            posts, total = website.get_posts(user_id, limit=limit, offset=offset)
        except Exception as e:
            traceback.print_exc()
            return _json(self, 500, {"error": f"list failed: {str(e)[:200]}"})

        # Echo the values actually applied (after clamping).
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        return _json(self, 200, {
            "posts": posts,
            "total": total,
            "limit": limit,
            "offset": offset,
        })
