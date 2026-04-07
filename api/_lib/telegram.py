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


def get_chat(chat_id_or_username) -> dict:
    return _post("getChat", {"chat_id": chat_id_or_username})


def get_chat_member(chat_id, user_id: int) -> dict:
    return _post("getChatMember", {"chat_id": chat_id, "user_id": user_id})


def get_me() -> dict:
    return _post("getMe", {})


def get_file(file_id: str) -> dict:
    return _post("getFile", {"file_id": file_id})


def download_file(file_path: str) -> bytes:
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def fetch_photo_bytes(file_id: str) -> tuple[bytes, str]:
    """Returns (bytes, mime). Telegram photos are JPEG."""
    info = get_file(file_id)
    if not info.get("ok"):
        raise RuntimeError(f"getFile failed: {info.get('error','')}")
    path = info["result"]["file_path"]
    return download_file(path), "image/jpeg"


def send_photo(chat_id: int, photo: str, caption: str = "", reply_markup: dict | None = None) -> dict:
    """photo can be a file_id or URL."""
    payload = {"chat_id": chat_id, "photo": photo}
    if caption:
        payload["caption"] = caption[:1024]
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("sendPhoto", payload)


def inline_kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} if data.startswith("cb:") else {"text": label, "url": data}
             for label, data in row]
            for row in rows
        ]
    }
