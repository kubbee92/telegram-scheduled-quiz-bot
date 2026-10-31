"""Web entry point.

  GET/POST /tick     called by an external cron service every ~5 minutes
  POST     /webhook  Telegram delivers admin messages here
  GET      /setup    one-time: registers the webhook with Telegram
  GET      /         health check
"""
import hmac
import logging

from flask import Flask, jsonify, request

import config
import handlers
import scheduler
from telegram_api import TelegramClient, TelegramError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)


def _same(a, b):
    return bool(b) and hmac.compare_digest(str(a).encode(), str(b).encode())


def _cron_authorized():
    if not config.CRON_SECRET:
        return None  # not configured
    key = request.args.get("key") or request.headers.get("X-Cron-Key", "")
    return _same(key, config.CRON_SECRET)


@app.get("/")
def index():
    return "quiz-bot is running"


@app.route("/tick", methods=["GET", "POST"])
def tick():
    ok = _cron_authorized()
    if ok is None:
        return jsonify(error="CRON_SECRET is not configured"), 503
    if not ok:
        return jsonify(error="forbidden"), 403
    try:
        return jsonify(scheduler.run_tick())
    except Exception:
        log.exception("tick failed")
        return jsonify(error="tick failed, see logs"), 500


@app.post("/webhook")
def webhook():
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not _same(given, config.WEBHOOK_SECRET):
        return "forbidden", 403
    try:
        handlers.handle_update(request.get_json(silent=True) or {})
    except Exception:
        # Always answer 200, otherwise Telegram keeps re-sending the same update.
        log.exception("update handling failed")
    return "ok"


@app.get("/setup")
def setup():
    ok = _cron_authorized()
    if ok is None:
        return jsonify(error="CRON_SECRET is not configured"), 503
    if not ok:
        return jsonify(error="forbidden"), 403
    if not (config.PUBLIC_URL and config.WEBHOOK_SECRET and config.BOT_TOKEN):
        return jsonify(error="set BOT_TOKEN, WEBHOOK_SECRET and PUBLIC_URL first"), 400
    tg = TelegramClient()
    try:
        tg.set_webhook(config.PUBLIC_URL + "/webhook", config.WEBHOOK_SECRET)
        info = tg.webhook_info()
    except TelegramError as e:
        return jsonify(error=str(e)), 502
    return jsonify(ok=True, webhook_url=info.get("url"), pending=info.get("pending_update_count"),
                   last_error=info.get("last_error_message"))


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
