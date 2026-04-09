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


def upload_image(access_token: str, owner_urn: str, image_bytes: bytes) -> str:
    """Initialize upload + PUT image via REST Images API. Returns image URN."""
    init_body = json.dumps({
        "initializeUploadRequest": {
            "owner": owner_urn,
        }
    }).encode()
    req = urllib.request.Request(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        data=init_body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202401",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())
    value = resp["value"]
    upload_url = value["uploadUrl"]
    image_urn = value["image"]
    put_req = urllib.request.Request(
        upload_url,
        data=image_bytes,
        method="PUT",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(put_req, timeout=60) as r:
        r.read()
    return image_urn


def create_post(access_token: str, author_urn: str, text: str, asset_urn: str | None = None) -> dict:
    """Create a post via REST Posts API. asset_urn is an image URN from upload_image."""
    payload = {
        "author": author_urn,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
    }
    if asset_urn:
        payload["content"] = {
            "media": {
                "title": "Image",
                "id": asset_urn,
            }
        }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.linkedin.com/rest/posts",
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202401",
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            post_id = r.headers.get("x-restli-id", "")
            return {"ok": True, "id": post_id}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:400]}
