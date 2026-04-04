"""
Utility endpoint to register your Telegram webhook.
Call this once after deployment:
  GET https://your-app.vercel.app/api/set_webhook
"""

import os
import json
from http.server import BaseHTTPRequestHandler
import httpx

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
VERCEL_URL = os.environ.get("VERCEL_URL", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not VERCEL_URL:
            result = {"error": "VERCEL_URL not set"}
        else:
            webhook_url = f"https://{VERCEL_URL}/api/webhook"
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
                    json={
                        "url": webhook_url,
                        "allowed_updates": ["message"],
                        "drop_pending_updates": True,
                    },
                )
                result = resp.json()
                result["webhook_url"] = webhook_url

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result, indent=2).encode())
