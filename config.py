"""Central configuration.

Secrets come from environment variables (set them in Ethiodeploy, never in code).
Everything else (schedule, limits) can be changed here.
"""
import os
from datetime import time, timedelta, timezone


def _int_set(value):
    return {int(x) for x in value.replace(" ", "").split(",") if x}


# ---- secrets / deployment (environment variables) --------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")          # from @BotFather
DATABASE_URL = os.environ.get("DATABASE_URL", "")    # Postgres connection string
CRON_SECRET = os.environ.get("CRON_SECRET", "")      # protects /tick and /setup
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")  # Telegram -> /webhook check
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")  # e.g. https://my-bot.ethiodeploy.app

# Telegram user IDs allowed to upload questions and use admin commands.
# Default is your ID; add more with ADMIN_IDS="5839558908,123456789".
ADMIN_IDS = _int_set(os.environ.get("ADMIN_IDS", "5839558908"))

# ---- schedule ---------------------------------------------------------------
# Ethiopia has no daylight saving time, so a fixed UTC+3 offset is exact.
ADDIS_TZ = timezone(timedelta(hours=3))
WINDOW_START = time(7, 0)         # quizzes are posted at random times
WINDOW_END = time(22, 0)          # between these two (Addis Ababa time)
QUESTIONS_PER_SUBJECT = 1         # questions per subject on each day it is scheduled
MIN_GAP = timedelta(minutes=5)    # minimum gap between two quizzes (they go to different topics)
GRACE = timedelta(hours=2)        # a quiz that is this late is skipped, not posted
STALE_AFTER = timedelta(minutes=10)
MAX_ATTEMPTS = 3                  # Telegram send attempts per quiz slot
LOW_QUEUE_DAYS = 3                # warn the admin when a subject has fewer questions than this

SUBJECTS = ["logic", "english", "physics", "geography", "math", "psychology", "history"]

# Which subjects are posted on which weekday (Monday = 0).
# Now: every subject, every day = 7 quizzes a day (49 a week), one per subject topic.
# To go back to one subject per day, replace this with e.g.
#   {0: ["logic"], 1: ["english"], 2: ["physics"], 3: ["geography"],
#    4: ["math"], 5: ["psychology"], 6: ["history"]}
SCHEDULE_BY_WEEKDAY = {d: list(SUBJECTS) for d in range(7)}
SUBJECT_ALIASES = {
    "maths": "math", "mathematics": "math",
    "eng": "english",
    "phys": "physics",
    "geo": "geography",
    "psych": "psychology",
}
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---- Telegram quiz limits (Bot API) ----------------------------------------
LIMIT_QUESTION = 300
LIMIT_OPTION = 100
LIMIT_EXPLANATION = 200

# ---- uploads ----------------------------------------------------------------
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000


def normalize_subject(text):
    """'Math ', 'MATHS' -> 'math'; unknown -> None."""
    s = " ".join(str(text or "").split()).casefold()
    s = SUBJECT_ALIASES.get(s, s)
    return s if s in SUBJECTS else None
