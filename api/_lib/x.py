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
SCOPES = "tweet.read tweet.write users.read offline.access"


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


def create_tweet(access_token: str, text: str) -> dict:
    body = json.dumps({"text": text}).encode()
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
