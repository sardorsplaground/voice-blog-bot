"""Upstash Redis REST client + helpers for users, OAuth state, drafts."""
import os
import json
import time
import secrets
import urllib.request
import urllib.parse
from typing import Optional, Any, Dict

URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def _req(*parts: str) -> Any:
    if not URL or not TOKEN:
        raise RuntimeError("UPSTASH_REDIS_REST_URL/TOKEN not set")
    path = "/".join(urllib.parse.quote(p, safe="") for p in parts)
    req = urllib.request.Request(
        f"{URL}/{path}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result")


def kv_set(key: str, value: str, ex: Optional[int] = None) -> None:
    if ex:
        _req("set", key, value, "EX", str(ex))
    else:
        _req("set", key, value)


def kv_get(key: str) -> Optional[str]:
    return _req("get", key)


def kv_del(key: str) -> None:
    _req("del", key)


# ---- Users ----
def user_key(tg_id: int) -> str:
    return f"user:{tg_id}"


def get_user(tg_id: int) -> Dict[str, Any]:
    raw = kv_get(user_key(tg_id))
    return json.loads(raw) if raw else {}


def save_user(tg_id: int, data: Dict[str, Any]) -> None:
    kv_set(user_key(tg_id), json.dumps(data))


def update_user(tg_id: int, **fields) -> Dict[str, Any]:
    user = get_user(tg_id)
    if not user:
        user = {"tg_id": tg_id, "created_at": int(time.time()), "plan": "free", "posts_used": 0, "posts_period_start": int(time.time())}
    user.update(fields)
    save_user(tg_id, user)
    return user


# ---- OAuth state (CSRF) ----
def make_oauth_state(tg_id: int, provider: str, **extra) -> str:
    state = secrets.token_urlsafe(24)
    payload = {"tg_id": tg_id, "provider": provider, **extra}
    kv_set(f"oauth_state:{state}", json.dumps(payload), ex=600)
    return state


def consume_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    raw = kv_get(f"oauth_state:{state}")
    if not raw:
        return None
    kv_del(f"oauth_state:{state}")
    return json.loads(raw)


# ---- Pending drafts ----
def save_draft(tg_id: int, draft: Dict[str, Any]) -> None:
    kv_set(f"draft:{tg_id}", json.dumps(draft), ex=3600)


def get_draft(tg_id: int) -> Optional[Dict[str, Any]]:
    raw = kv_get(f"draft:{tg_id}")
    return json.loads(raw) if raw else None


def clear_draft(tg_id: int) -> None:
    kv_del(f"draft:{tg_id}")


# ---- Usage / quota ----
FREE_LIMIT = 10
PERIOD_SECONDS = 30 * 24 * 3600


def check_and_increment_quota(tg_id: int) -> tuple[bool, int, int]:
    """Returns (allowed, used_after, limit)."""
    user = get_user(tg_id) or {}
    now = int(time.time())
    period_start = user.get("posts_period_start", now)
    used = user.get("posts_used", 0)
    if now - period_start > PERIOD_SECONDS:
        period_start = now
        used = 0
    plan = user.get("plan", "free")
    limit = FREE_LIMIT if plan == "free" else 10**9
    if used >= limit:
        return False, used, limit
    used += 1
    update_user(tg_id, posts_used=used, posts_period_start=period_start)
    return True, used, limit
