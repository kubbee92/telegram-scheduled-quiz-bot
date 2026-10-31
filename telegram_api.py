"""Minimal Telegram Bot API client (uses only `requests`)."""
import logging

import requests

import config

log = logging.getLogger(__name__)
API = "https://api.telegram.org"


class TelegramError(Exception):
    """A failed Bot API call.

    definitive=True  -> Telegram answered with an error (or we never connected),
                        so the action certainly did NOT happen; safe to retry.
    definitive=False -> outcome unknown (timeout mid-request, 5xx, bad reply):
                        a message may or may not have been posted.
    """

    def __init__(self, message, code=None, definitive=False):
        super().__init__(message)
        self.code = code
        self.definitive = definitive


class TelegramClient:
    def __init__(self, token=None):
        self.token = token or config.BOT_TOKEN

    # The token is part of every request URL, so scrub it from any error text.
    def _redact(self, text):
        text = str(text)
        return text.replace(self.token, "***") if self.token else text

    def _call(self, method, payload=None, files=None, timeout=(10, 40)):
        if not self.token:
            raise TelegramError("BOT_TOKEN is not set", definitive=True)
        url = f"{API}/bot{self.token}/{method}"
        try:
            if files:
                resp = requests.post(url, data=payload, files=files, timeout=timeout)
            else:
                resp = requests.post(url, json=payload, timeout=timeout)
        except requests.ConnectTimeout as e:
            raise TelegramError(self._redact(f"connect timeout: {e}"), definitive=True)
        except requests.RequestException as e:
            raise TelegramError(self._redact(f"network error: {e}"), definitive=False)
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(
                f"HTTP {resp.status_code} (non-JSON reply)",
                code=resp.status_code, definitive=False,
            )
        if data.get("ok"):
            return data["result"]
        code = data.get("error_code", resp.status_code)
        raise TelegramError(
            self._redact(f"{code}: {data.get('description')}"),
            code=code, definitive=code < 500,
        )

    # ---- messages -----------------------------------------------------------
    def send_message(self, chat_id, text, thread_id=None):
        payload = {"chat_id": chat_id, "text": text[:4000]}
        if thread_id:
            payload["message_thread_id"] = thread_id
        return self._call("sendMessage", payload)

    def send_quiz(self, chat_id, thread_id, q):
        """Post an anonymous quiz-mode poll. `q` has question/options/correct/explanation."""
        payload = {
            "chat_id": chat_id,
            "question": q["question"],
            "options": list(q["options"]),
            "type": "quiz",
            "is_anonymous": True,
            "allows_multiple_answers": False,
            "correct_option_id": q["correct"],
        }
        if q.get("explanation"):
            payload["explanation"] = q["explanation"]
        if thread_id:
            payload["message_thread_id"] = thread_id
        return self._call("sendPoll", payload)

    def send_document(self, chat_id, filename, content, caption=None):
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        return self._call("sendDocument", payload, files={"document": (filename, content)})

    # ---- files --------------------------------------------------------------
    def download_file(self, file_id, max_bytes):
        info = self._call("getFile", {"file_id": file_id})
        url = f"{API}/file/bot{self.token}/{info['file_path']}"
        try:
            with requests.get(url, stream=True, timeout=(10, 60)) as r:
                r.raise_for_status()
                buf = bytearray()
                for chunk in r.iter_content(65536):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise TelegramError("file too large", definitive=True)
                return bytes(buf)
        except requests.RequestException as e:
            raise TelegramError(self._redact(f"download failed: {e}"), definitive=True)

    # ---- webhook ------------------------------------------------------------
    def set_webhook(self, url, secret_token):
        return self._call("setWebhook", {
            "url": url,
            "secret_token": secret_token,
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        })

    def webhook_info(self):
        return self._call("getWebhookInfo", {})
