"""Multi-tenant blog publishing — internal platform integration.

Follows the same shape as linkedin.py / x.py, but instead of calling a
third-party API we persist posts to our own Upstash Redis via db.py.

Public functions:
    publish_post(user_id, title, content, image_url, tags, slug)
    get_post(user_id, slug)
    get_posts(user_id, limit, offset)  -> (posts, total)
    delete_post(user_id, slug)
"""
import re
import time
import secrets
from typing import Optional, Dict, Any, List, Tuple

from api._lib import db


MAX_SLUG_LEN = 80
MAX_TITLE_LEN = 200
MAX_CONTENT_LEN = 60_000  # ~ generous ceiling for markdown blog posts
MAX_TAGS = 10
MAX_TAG_LEN = 30
MAX_LIMIT = 50
DEFAULT_LIMIT = 10


def _slugify(s: str, max_len: int = MAX_SLUG_LEN) -> str:
    s = (s or "").strip().lower()
    # Replace anything that isn't alphanumeric (in any language) or whitespace with nothing,
    # then collapse whitespace/dashes/underscores into single dashes.
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    # If non-ASCII letters are present, Upstash REST path accepts them when quoted,
    # so we keep them. But trim to a safe length.
    return s[:max_len] or "post"


def _unique_slug(user_id: int, base: str) -> str:
    slug = _slugify(base)
    if not db.blog_slug_exists(user_id, slug):
        return slug
    # Avoid O(n) probing in the pathological case by falling back to a short suffix.
    for i in range(2, 20):
        candidate = f"{slug}-{i}"[:MAX_SLUG_LEN]
        if not db.blog_slug_exists(user_id, candidate):
            return candidate
    return f"{slug[:MAX_SLUG_LEN - 8]}-{secrets.token_hex(3)}"


def _clean_tags(tags: Optional[List[Any]]) -> List[str]:
    if not tags:
        return []
    out: List[str] = []
    seen = set()
    for t in tags:
        if not isinstance(t, (str, int, float)):
            continue
        tag = str(t).strip().lower()
        if not tag:
            continue
        tag = tag[:MAX_TAG_LEN]
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out


def publish_post(
    user_id: int,
    title: str,
    content: str,
    image_url: Optional[str] = None,
    tags: Optional[List[Any]] = None,
    slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new blog post. Raises ValueError on invalid input."""
    if user_id is None:
        raise ValueError("user_id is required")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise ValueError("user_id must be an integer")

    title = (title or "").strip()
    content = (content or "").strip()
    if not title:
        raise ValueError("title is required")
    if not content:
        raise ValueError("content is required")
    if len(title) > MAX_TITLE_LEN:
        title = title[:MAX_TITLE_LEN].rstrip()
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError(f"content exceeds {MAX_CONTENT_LEN} characters")

    if slug:
        wanted = _slugify(slug)
        final_slug = wanted if not db.blog_slug_exists(user_id_int, wanted) else _unique_slug(user_id_int, wanted)
    else:
        final_slug = _unique_slug(user_id_int, title)

    now = int(time.time())
    post = {
        "id": f"{user_id_int}:{final_slug}",
        "user_id": user_id_int,
        "title": title,
        "slug": final_slug,
        "content": content,
        "image_url": (image_url or "").strip(),
        "tags": _clean_tags(tags),
        "published_at": now,
        "updated_at": now,
    }
    db.blog_put(user_id_int, final_slug, post, now)
    return post


def get_post(user_id: int, slug: str) -> Optional[Dict[str, Any]]:
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return None
    if not slug:
        return None
    return db.blog_get(user_id_int, slug)


def get_posts(user_id: int, limit: int = DEFAULT_LIMIT, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return [], 0
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    return db.blog_list(user_id_int, limit=limit, offset=offset)


def delete_post(user_id: int, slug: str) -> bool:
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return False
    if not slug:
        return False
    return db.blog_delete(user_id_int, slug)
