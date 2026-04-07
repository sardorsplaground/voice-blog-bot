"""LinkedIn OAuth + Posts API."""
import os
import json
import time
import urllib.request
import urllib.parse

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
BASE = os.environ.get("APP_BASE_URL", "https://postr.ai")
REDIRECT_URI = f"{BASE}/api/oauth/linkedin/callback"
SCOPES = "openid profile email w_member_social"


def authorize_url(state: str) -> str:
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": SCOPES,
    })
    return f"https://www.linkedin.com/oauth/v2/authorization?{q}"


def exchange_code(code: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def get_userinfo(access_token: str) -> dict:
    req = urllib.request.Request(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def create_post(access_token: str, author_urn: str, text: str) -> dict:
    """author_urn looks like 'urn:li:person:abc123'."""
    body = json.dumps({
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }).encode()
    req = urllib.request.Request(
        "https://api.linkedin.com/v2/ugcPosts",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read() or b"{}")
            return {"ok": True, "id": data.get("id") or r.headers.get("x-restli-id")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:400]}
