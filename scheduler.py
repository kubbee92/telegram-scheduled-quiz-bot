"""The "tick": called every few minutes by an external cron service.

Each tick is idempotent and cheap:
  1. make sure today's quiz times exist (random, inside the evening window)
  2. post every quiz whose time has come
  3. warn admins about empty / low queues and anything that went wrong
It keeps no state in memory, so it works even if the host sleeps between pings.
"""
import logging
import random
from datetime import datetime, timedelta, timezone

import config
import db
from telegram_api import TelegramClient, TelegramError

log = logging.getLogger(__name__)
UTC = timezone.utc


def plan_slots(day, now, rng, n):
    start = datetime.combine(day, config.WINDOW_START, tzinfo=config.ADDIS_TZ)
    end = datetime.combine(day, config.WINDOW_END, tzinfo=config.ADDIS_TZ)
    start = max(start, now + timedelta(minutes=1))
    gap = config.MIN_GAP
    free = (end - start) - gap * (n - 1)
    if free <= timedelta(0):
        return [start + gap * i for i in range(n)]
    offsets = sorted(rng.uniform(0, free.total_seconds()) for _ in range(n))
    return [start + timedelta(seconds=o) + gap * i for i, o in enumerate(offsets)]

def ensure_schedule(now, rng):
    day = now.astimezone(config.ADDIS_TZ).date()
    end = datetime.combine(day, config.WINDOW_END, tzinfo=config.ADDIS_TZ)
    late = now >= end
    with db.tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(724001)")  # one planner at a time
        cur.execute("SELECT count(*) AS n FROM schedule WHERE day = %s", (day,))
        if cur.fetchone()["n"]:
            return None
        
        # Base: 1 question for all 7 subjects scheduled for today
        base_subjects = config.SCHEDULE_BY_WEEKDAY[day.weekday()]
        subjects = list(base_subjects)
        
        # Extra: 4 random questions from those same 7 subjects
        extra_subjects = rng.sample(base_subjects, 4)
        subjects.extend(extra_subjects)
        
        # Plan times for all 11 questions
        times = plan_slots(day, now, rng, len(subjects))
        
        for slot_idx, subject in enumerate(subjects, start=1):
            t = times[slot_idx - 1]
            cur.execute(
                """INSERT INTO schedule (day, slot, subject, planned_at, status)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (day, slot) DO NOTHING""",
                (day, slot_idx, subject, t, "missed" if late else "planned"),
            )
    return "late" if late else "planned"


def _is_config_error(e):
    """Errors that say 'the bot/group is set up wrong', not 'this question is bad'."""
    desc = str(e).lower()
    return (
        e.code in (401, 403, 404, 429)
        or (e.code or 0) >= 500
        or any(k in desc for k in (
            "chat not found", "thread not found", "not enough rights", "kicked",
            "forbidden", "not a member", "have no rights", "topic",
        ))
    )


class Alerts:
    """Sends admin notifications; each (day, subject, kind) is sent at most once."""

    def __init__(self, tg):
        self.tg = tg

    def notify_admins(self, text):
        for admin_id in config.ADMIN_IDS:
            try:
                self.tg.send_message(admin_id, text)
            except TelegramError as e:
                # Usually: the admin never pressed Start in a chat with the bot.
                log.warning("could not notify admin %s: %s", admin_id, e)

    def once(self, day, subject, kind, text):
        if db.alert_once(day, subject, kind):
            self.notify_admins(text)
            return True
        return False


def _name(subject):
    return subject.capitalize()


def _recover_stale(now, alerts):
    for s in db.stale_sending(now):
        db.resolve_stale(s["id"], s["question_id"])
        preview = (s["question"] or "")[:60]
        alerts.once(
            s["day"], s["subject"], "unconfirmed",
            f"❓ {_name(s['subject'])}: a quiz was sent to Telegram but I never got confirmation "
            f"(network problem?). Please check the group topic. I treated it as posted, "
            f"so it will not be sent again.\nQuestion: {preview}",
        )


def _process_slot(slot, now, tg, alerts, summary):
    subject, day = slot["subject"], slot["day"]
    topics = db.get_topics(subject)
    
    if not topics:
        alerts.once(
            day, subject, "no_topic",
            f"⚙️ No group topic is set for {_name(subject)}. Open that topic in the group and "
            f"send: /settopic {subject}\nThe quiz will go out automatically once it is set.",
        )
        summary["blocked"] += 1
        return

    state, q = db.claim_next_question(slot["id"], subject, now)
    if state == "taken":
        return
    if state == "empty":
        alerts.once(
            day, subject, "empty",
            f"📭 The {_name(subject)} queue is empty, so today's {_name(subject)} quiz was not "
            f"posted. Send me an Excel file with more {_name(subject)} questions — if you do it "
            f"within the next couple of hours it will still go out today.",
        )
        summary["blocked"] += 1
        return

    success_count = 0
    last_error = None
    unconfirmed = False
    
    # Loop through all registered groups and send the quiz to each
    for topic in topics:
        try:
            tg.send_quiz(topic["chat_id"], topic["thread_id"], q)
            success_count += 1
        except TelegramError as e:
            last_error = e
            if not e.definitive:
                unconfirmed = True
                log.error("send outcome unknown for slot %s: %s", slot["id"], e)

    # If it failed to send to ALL groups
    if success_count == 0:
        if unconfirmed:
            summary["unconfirmed"] += 1
            return  # stays 'sending'; _recover_stale reports it after STALE_AFTER
            
        status = db.release_claim(slot["id"], q["id"], str(last_error), burn_question=not _is_config_error(last_error))
        if status == "failed":
            summary["failed"] += 1
            alerts.once(
                day, subject, "failed",
                f"🚫 I couldn't post the {_name(subject)} quiz after {config.MAX_ATTEMPTS} "
                f"attempts.\nTelegram said: {last_error}\n"
                f"Check that the bot is an admin of the group and the topic still exists.",
            )
        return

    db.mark_sent(slot["id"], q["id"], now)
    summary["sent"] += 1

    remaining = db.pending_count(subject)
    if remaining < config.QUESTIONS_PER_DAY:
        weekday = day.strftime("%A")
        if remaining == 0:
            text = (f"📭 The {_name(subject)} queue is now empty. Next {weekday}'s quiz needs "
                    f"{config.QUESTIONS_PER_DAY} new questions — please upload more.")
        else:
            text = (f"⚠️ The {_name(subject)} queue is running low ({remaining} left). Next {weekday}'s "
                    f"quiz needs {config.QUESTIONS_PER_DAY} questions.")
        alerts.once(day, subject, "low_queue", text)


def run_tick(now=None, tg=None, rng=None):
    now = now or datetime.now(UTC)
    tg = tg or TelegramClient()
    rng = rng or random.Random()
    alerts = Alerts(tg)
    summary = {
        "time": now.astimezone(config.ADDIS_TZ).isoformat(timespec="minutes"),
        "sent": 0, "blocked": 0, "failed": 0, "unconfirmed": 0,
    }

    db.ensure_schema()
    _recover_stale(now, alerts)

    day = now.astimezone(config.ADDIS_TZ).date()
    if ensure_schedule(now, rng) == "late":
        alerts.once(
            day, "System", "missed",
            "⏰ The service woke up late, so today's 11 quizzes were skipped."
        )

    for m in db.expire_old_slots(now):
        # Skip the "missed" notice if an earlier alert already explains it.
        if not db.has_alert(m["day"], m["subject"], ("empty", "no_topic", "failed", "missed")):
            alerts.once(
                m["day"], m["subject"], "missed",
                f"⏰ A {_name(m['subject'])} quiz was not posted in time and was skipped. "
                f"(Is the cron ping running every 5 minutes?)",
            )

    for slot in db.due_slots(now):
        _process_slot(slot, now, tg, alerts, summary)

    log.info("tick %s", summary)
    return summary
