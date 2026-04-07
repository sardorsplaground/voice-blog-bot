"""Telegram Bot API helpers."""
import os
import json
import urllib.request
import urllib.parse

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _post(method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{API}/{method}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:300]}


def send_message(chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _post("sendMessage", payload)


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None) -> dict:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _post("editMessageText", payload)


def answer_callback(callback_id: str, text: str = "") -> dict:
    return _post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def inline_kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} if data.startswith("cb:") else {"text": label, "url": data}
             for label, data in row]
            for row in rows
        ]
    }
