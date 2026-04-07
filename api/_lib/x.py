"""X (Twitter) OAuth 2.0 PKCE + Tweets API."""
import os
import json
import base64
import hashlib
import secrets
import urllib.request
import urllib.parse

CLIENT_ID = os.environ.get("X_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("X_CLIENT_SECRET", "")
BASE = os.environ.get("APP_BASE_URL", "https://postr.ai")
REDIRECT_URI = f"{BASE}/api/oauth/x/callback"
SCOPES = "tweet.read tweet.write users.read offline.access media.write"


def gen_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def authorize_url(state: str, code_challenge: str) -> str:
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"https://twitter.com/i/oauth2/authorize?{q}"


def _basic_auth_header() -> str:
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def exchange_code(code: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "client_id": CLIENT_ID,
    }).encode()
    headers = {"content-type": "application/x-www-form-urlencoded"}
    if CLIENT_SECRET:
        headers["Authorization"] = _basic_auth_header()
    req = urllib.request.Request("https://api.twitter.com/2/oauth2/token", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def refresh_access(refresh_token: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    }).encode()
    headers = {"content-type": "application/x-www-form-urlencoded"}
    if CLIENT_SECRET:
        headers["Authorization"] = _basic_auth_header()
    req = urllib.request.Request("https://api.twitter.com/2/oauth2/token", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def get_me(access_token: str) -> dict:
    req = urllib.request.Request(
        "https://api.twitter.com/2/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def upload_media(access_token: str, image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Upload media via v2 endpoint. Returns media_id string."""
    boundary = "----postrai" + secrets.token_hex(8)
    filename = "image.jpg" if "jpeg" in mime else "image.png"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    body = pre + image_bytes + post
    req = urllib.request.Request(
        "https://api.x.com/2/media/upload",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return str(data.get("data", {}).get("id") or data.get("media_id_string") or data.get("id"))


def create_tweet(access_token: str, text: str, media_id: str | None = None) -> dict:
    payload = {"text": text}
    if media_id:
        payload["media"] = {"media_ids": [media_id]}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.twitter.com/2/tweets",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
            return {"ok": True, "id": data.get("data", {}).get("id")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:400]}
