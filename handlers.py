"""Incoming Telegram messages: admin commands and Excel uploads."""
import logging
from datetime import datetime, timezone

import config
import db
import excel_io
from telegram_api import TelegramClient, TelegramError

log = logging.getLogger(__name__)

HELP = (
    "📘 Quiz bot — admin help\n\n"
    "• Send me an .xlsx file here to add questions (get the format with /template)\n"
    "• /queue — questions left per subject\n"
    "• /today — today's planned post times\n"
    "• /topics — which group topic each subject posts to\n"
    "• /template — Excel template\n\n"
    "In the group, inside each subject's topic, send: /settopic <subject>\n"
    "Subjects: " + ", ".join(config.SUBJECTS) + "\n\n"
    f"Schedule: every day, {config.QUESTIONS_PER_SUBJECT} question per subject "
    f"({len(config.SUBJECTS)} quizzes a day), at random times between "
    f"{config.WINDOW_START:%H:%M} and {config.WINDOW_END:%H:%M}."
)


def _is_admin(msg):
    user = msg.get("from") or {}
    # Anonymous group admins appear as sender_chat; they can't be verified, so ignore.
    return user.get("id") in config.ADMIN_IDS and not msg.get("sender_chat")


def handle_update(update, tg=None):
    msg = update.get("message")
    if not msg or "chat" not in msg:
        return
    tg = tg or TelegramClient()
    db.ensure_schema()

    chat = msg["chat"]
    private = chat.get("type") == "private"
    admin = _is_admin(msg)
    thread = msg.get("message_thread_id") if msg.get("is_topic_message") else None

    def reply(text):
        try:
            tg.send_message(chat["id"], text, thread_id=thread)
        except TelegramError as e:
            log.warning("reply failed: %s", e)

    if not admin:
        if private:
            reply("Sorry, this bot is private.")
        return

    text = (msg.get("text") or "").strip()
    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        _command(cmd, args, msg, chat, private, thread, reply, tg)
    elif msg.get("document") and private:
        _upload(msg, chat, reply, tg)
    elif private:
        reply("Send me an .xlsx file with questions, or /help.")


# ---- commands -----------------------------------------------------------------
def _command(cmd, args, msg, chat, private, thread, reply, tg):
    if cmd in ("/start", "/help"):
        reply(HELP)
    elif cmd == "/settopic":
        _settopic(args, chat, private, thread, reply)
    elif cmd == "/topics":
        reply(_topics_text())
    elif cmd == "/queue":
        reply(_queue_text())
    elif cmd == "/today":
        reply(_today_text())
    elif cmd == "/template":
        try:
            tg.send_document(chat["id"], "questions_template.xlsx", excel_io.build_template(),
                             caption="Fill this in and send it back to me.")
        except TelegramError as e:
            log.warning("template send failed: %s", e)
    elif private:
        reply("Unknown command. Try /help.")


def _settopic(args, chat, private, thread, reply):
    if private:
        reply("Send /settopic <subject> inside the group topic you want that subject posted to.")
        return
    subject = config.normalize_subject(" ".join(args))
    if not subject:
        reply("Usage: /settopic <subject>\nSubjects: " + ", ".join(config.SUBJECTS))
        return
    db.set_topic(subject, chat["id"], thread or 0)
    where = "this topic" if thread else "the group's General chat"
    reply(f"✅ {subject.capitalize()} quizzes will be posted in {where}.")


def _topics_text():
    topics = db.all_topics()
    lines = ["📍 Subject → group topic"]
    for subject in config.SUBJECTS:
        t = topics.get(subject)
        if t:
            where = f"topic {t['thread_id']}" if t["thread_id"] else "General"
            lines.append(f"{subject.capitalize()}: {where} (chat {t['chat_id']})")
        else:
            lines.append(f"{subject.capitalize()}: ❌ not set — use /settopic {subject}")
    return "\n".join(lines)


def _queue_text():
    counts = db.queue_counts()
    lines = ["📚 Questions left per subject"]
    for subject in config.SUBJECTS:
        left = counts.get(subject, {}).get("pending", 0)
        days = left // config.QUESTIONS_PER_SUBJECT
        flag = "⚠️ " if left < config.LOW_QUEUE_DAYS * config.QUESTIONS_PER_SUBJECT else ""
        lines.append(f"{flag}{subject.capitalize()}: {left} left ≈ {days} day{'s' if days != 1 else ''}")
    return "\n".join(lines)


def _today_text():
    day = datetime.now(timezone.utc).astimezone(config.ADDIS_TZ).date()
    slots = db.today_slots(day)
    if not slots:
        return ("Nothing planned yet for today. Times are chosen at the first check of the day "
                "(the cron ping).")
    lines = [f"🗓 {day.strftime('%A')} — {len(slots)} quizzes"]
    for s in sorted(slots, key=lambda s: s["planned_at"]):
        t = s["planned_at"].astimezone(config.ADDIS_TZ).strftime("%H:%M")
        lines.append(f"{t} — {s['subject'].capitalize()} — {s['status']}")
    return "\n".join(lines)


# ---- Excel upload ---------------------------------------------------------------
def _upload(msg, chat, reply, tg):
    doc = msg["document"]
    name = doc.get("file_name", "")
    if not name.lower().endswith(".xlsx"):
        reply("Please send an Excel .xlsx file (get the format with /template).")
        return
    if doc.get("file_size", 0) > config.MAX_UPLOAD_BYTES:
        reply("That file is too large (max 5 MB).")
        return

    try:
        data = tg.download_file(doc["file_id"], config.MAX_UPLOAD_BYTES)
    except TelegramError as e:
        reply(f"❌ I couldn't download the file: {e}")
        return

    try:
        result = excel_io.parse_workbook(data)
    except excel_io.ExcelFormatError as e:
        reply(f"❌ {e}")
        return

    added, db_dups = db.insert_questions(result.rows, msg["from"]["id"])
    dups = len(result.duplicates) + db_dups

    lines = []
    total = sum(added.values())
    if total:
        lines.append(f"✅ Added {total} question{'s' if total != 1 else ''}")
        for subject in config.SUBJECTS:
            if added.get(subject):
                lines.append(f"• {subject.capitalize()}: {added[subject]}")
    else:
        lines.append("Nothing was added.")
    if dups:
        lines.append(f"↩️ Skipped {dups} duplicate{'s' if dups != 1 else ''} (same question text "
                     f"already in the file, queue or history)")
    if result.samples:
        lines.append(f"Skipped {result.samples} [SAMPLE] row{'s' if result.samples != 1 else ''}")
    if result.errors:
        lines.append(f"❌ {len(result.errors)} row{'s' if len(result.errors) != 1 else ''} rejected:")
        for rownum, why in result.errors[:12]:
            lines.append(f"• Row {rownum}: {why}")
        if len(result.errors) > 12:
            lines.append(f"…and {len(result.errors) - 12} more")
        lines.append("Fix those rows and send the file again — rows already added are skipped as duplicates.")
    lines.append("")
    lines.append(_queue_text())
    reply("\n".join(lines))
