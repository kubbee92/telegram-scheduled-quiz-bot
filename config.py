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
WINDOW_START = time(18, 30)       # quizzes are posted at random times
WINDOW_END = time(21, 0)          # between these two (Addis Ababa time)
QUESTIONS_PER_DAY = 2             # per subject day  ->  14 per week
MIN_GAP = timedelta(minutes=30)   # minimum gap between the day's quizzes
GRACE = timedelta(hours=2)        # a quiz that is this late is skipped, not posted
STALE_AFTER = timedelta(minutes=10)
MAX_ATTEMPTS = 3                  # Telegram send attempts per quiz slot

SUBJECT_BY_WEEKDAY = {            # Monday = 0
    0: "logic",
    1: "english",
    2: "physics",
    3: "geography",
    4: "math",
    5: "psychology",
    6: "history",
}
SUBJECTS = list(SUBJECT_BY_WEEKDAY.values())
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
