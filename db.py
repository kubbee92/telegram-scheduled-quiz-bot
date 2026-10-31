"""Postgres storage: question queue, topic mapping, daily schedule, alert log.

Every function opens a short transaction. No network calls to Telegram happen
inside a transaction, so a slow Telegram never holds database locks.
"""
import contextlib
from collections import Counter

import psycopg2
import psycopg2.extras

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id           BIGSERIAL PRIMARY KEY,
    subject      TEXT NOT NULL,
    question     TEXT NOT NULL,
    options      JSONB NOT NULL,
    correct      INT NOT NULL,
    explanation  TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|sending|sent|rejected
    uploaded_by  BIGINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at      TIMESTAMPTZ,
    UNIQUE (subject, content_hash)
);
CREATE INDEX IF NOT EXISTS questions_queue_idx ON questions (subject, status, id);

CREATE TABLE IF NOT EXISTS topics (
    subject   TEXT PRIMARY KEY,
    chat_id   BIGINT NOT NULL,
    thread_id INT NOT NULL DEFAULT 0              -- 0 = General / no topic
);

CREATE TABLE IF NOT EXISTS schedule (
    id          BIGSERIAL PRIMARY KEY,
    day         DATE NOT NULL,                    -- Addis Ababa local date
    slot        INT NOT NULL,
    subject     TEXT NOT NULL,
    planned_at  TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL DEFAULT 'planned',  -- planned|sending|sent|failed|missed|unconfirmed
    question_id BIGINT REFERENCES questions(id),
    attempts    INT NOT NULL DEFAULT 0,
    claimed_at  TIMESTAMPTZ,
    note        TEXT,
    UNIQUE (day, slot)
);

CREATE TABLE IF NOT EXISTS alert_log (
    day     DATE NOT NULL,
    subject TEXT NOT NULL,
    kind    TEXT NOT NULL,
    PRIMARY KEY (day, subject, kind)
);
"""

_schema_ready = False


@contextlib.contextmanager
def tx():
    """One short transaction; commits on success, rolls back on error."""
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(
        config.DATABASE_URL,
        connect_timeout=15,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def ensure_schema(force=False):
    global _schema_ready
    if _schema_ready and not force:
        return
    with tx() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(724000)")
        cur.execute(SCHEMA)
    _schema_ready = True


# ---- topics -----------------------------------------------------------------
def set_topic(subject, chat_id, thread_id):
    with tx() as cur:
        cur.execute(
            """INSERT INTO topics (subject, chat_id, thread_id) VALUES (%s, %s, %s)
               ON CONFLICT (subject) DO UPDATE
               SET chat_id = EXCLUDED.chat_id, thread_id = EXCLUDED.thread_id""",
            (subject, chat_id, thread_id),
        )


def get_topic(subject):
    with tx() as cur:
        cur.execute("SELECT chat_id, thread_id FROM topics WHERE subject = %s", (subject,))
        return cur.fetchone()


def all_topics():
    with tx() as cur:
        cur.execute("SELECT subject, chat_id, thread_id FROM topics")
        return {r["subject"]: r for r in cur.fetchall()}


# ---- questions --------------------------------------------------------------
def insert_questions(rows, uploaded_by):
    """Insert parsed rows in file order. Returns (Counter by subject, duplicate count)."""
    if not rows:
        return Counter(), 0
    values = [
        (r.subject, r.question, psycopg2.extras.Json(r.options), r.correct,
         r.explanation, r.content_hash, uploaded_by)
        for r in rows
    ]
    with tx() as cur:
        inserted = psycopg2.extras.execute_values(
            cur,
            """INSERT INTO questions
                   (subject, question, options, correct, explanation, content_hash, uploaded_by)
               VALUES %s
               ON CONFLICT (subject, content_hash) DO NOTHING
               RETURNING subject""",
            values,
            page_size=500,
            fetch=True,
        )
    added = Counter(r["subject"] for r in inserted)
    return added, len(rows) - sum(added.values())


def queue_counts():
    """{subject: {status: n}}"""
    out = {}
    with tx() as cur:
        cur.execute("SELECT subject, status, count(*) AS n FROM questions GROUP BY 1, 2")
        for r in cur.fetchall():
            out.setdefault(r["subject"], {})[r["status"]] = r["n"]
    return out


def pending_count(subject):
    with tx() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM questions WHERE subject = %s AND status = 'pending'",
            (subject,),
        )
        return cur.fetchone()["n"]


# ---- schedule ---------------------------------------------------------------
def today_slots(day):
    with tx() as cur:
        cur.execute("SELECT * FROM schedule WHERE day = %s ORDER BY slot", (day,))
        return cur.fetchall()


def claim_next_question(slot_id, subject, now):
    """Atomically reserve the oldest pending question for a due slot.

    Returns (state, question) with state in 'ok' | 'empty' | 'taken'.
    SKIP LOCKED means two overlapping ticks can never grab the same row.
    """
    with tx() as cur:
        cur.execute(
            "SELECT id FROM schedule WHERE id = %s AND status = 'planned' FOR UPDATE SKIP LOCKED",
            (slot_id,),
        )
        if cur.fetchone() is None:
            return "taken", None
        cur.execute(
            """SELECT id, question, options, correct, explanation FROM questions
               WHERE subject = %s AND status = 'pending'
               ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""",
            (subject,),
        )
        q = cur.fetchone()
        if q is None:
            return "empty", None
        cur.execute("UPDATE questions SET status = 'sending' WHERE id = %s", (q["id"],))
        cur.execute(
            "UPDATE schedule SET status = 'sending', question_id = %s, claimed_at = %s WHERE id = %s",
            (q["id"], now, slot_id),
        )
        return "ok", q


def mark_sent(slot_id, question_id, now):
    with tx() as cur:
        cur.execute(
            "UPDATE questions SET status = 'sent', sent_at = %s WHERE id = %s", (now, question_id)
        )
        cur.execute("UPDATE schedule SET status = 'sent' WHERE id = %s", (slot_id,))


def release_claim(slot_id, question_id, error, burn_question):
    """Telegram definitively refused. Retry later, or give up after MAX_ATTEMPTS.

    Returns the slot's new status ('planned' or 'failed').
    burn_question=True marks the question 'rejected' once attempts run out
    (used when the question content itself is the problem).
    """
    with tx() as cur:
        cur.execute(
            "UPDATE schedule SET attempts = attempts + 1, note = %s WHERE id = %s RETURNING attempts",
            (error[:500], slot_id),
        )
        attempts = cur.fetchone()["attempts"]
        give_up = attempts >= config.MAX_ATTEMPTS
        new_slot_status = "failed" if give_up else "planned"
        new_q_status = "rejected" if (give_up and burn_question) else "pending"
        cur.execute(
            "UPDATE schedule SET status = %s, question_id = NULL, claimed_at = NULL WHERE id = %s",
            (new_slot_status, slot_id),
        )
        cur.execute("UPDATE questions SET status = %s WHERE id = %s", (new_q_status, question_id))
        return new_slot_status


def stale_sending(now):
    """Slots stuck in 'sending': we never learned whether Telegram posted them."""
    with tx() as cur:
        cur.execute(
            """SELECT s.id, s.day, s.subject, s.question_id, q.question
               FROM schedule s LEFT JOIN questions q ON q.id = s.question_id
               WHERE s.status = 'sending' AND s.claimed_at < %s""",
            (now - config.STALE_AFTER,),
        )
        return cur.fetchall()


def resolve_stale(slot_id, question_id):
    # At-most-once: assume it WAS posted, so students never see a question twice.
    with tx() as cur:
        cur.execute("UPDATE schedule SET status = 'unconfirmed' WHERE id = %s", (slot_id,))
        if question_id:
            cur.execute("UPDATE questions SET status = 'sent' WHERE id = %s", (question_id,))


def expire_old_slots(now):
    with tx() as cur:
        cur.execute(
            """UPDATE schedule SET status = 'missed'
               WHERE status = 'planned' AND planned_at < %s RETURNING day, subject""",
            (now - config.GRACE,),
        )
        return cur.fetchall()


def due_slots(now):
    with tx() as cur:
        cur.execute(
            "SELECT * FROM schedule WHERE status = 'planned' AND planned_at <= %s ORDER BY planned_at, id",
            (now,),
        )
        return cur.fetchall()


# ---- alert de-duplication ----------------------------------------------------
def alert_once(day, subject, kind):
    """True the first time (day, subject, kind) is seen; False afterwards."""
    with tx() as cur:
        cur.execute(
            "INSERT INTO alert_log (day, subject, kind) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (day, subject, kind),
        )
        return cur.rowcount == 1


def has_alert(day, subject, kinds):
    with tx() as cur:
        cur.execute(
            "SELECT 1 FROM alert_log WHERE day = %s AND subject = %s AND kind = ANY(%s) LIMIT 1",
            (day, subject, list(kinds)),
        )
        return cur.fetchone() is not None
