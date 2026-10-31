# Telegram Quiz Bot

Posts anonymous **quiz-mode polls** (one correct answer + short explanation) to a Telegram
group, one subject per weekday, 2 quizzes per day at random evening times
(18:30–21:00 Addis Ababa). Admins add questions by sending an **Excel file** to the bot.

| Day | Subject | Day | Subject |
|-----|---------|-----|---------|
| Mon | Logic | Fri | Math |
| Tue | English | Sat | Psychology |
| Wed | Physics | Sun | History |
| Thu | Geography | | |

2 quizzes × 7 days = **14 questions/week**. Each subject posts into its own topic of the same group.

## How it works

```
cron-job.org ──every 5 min──► /tick   (plans today's random times, posts what is due)
Telegram ────admin messages──► /webhook (Excel uploads, /settopic, /queue ...)
                     both read/write ──► Postgres (questions, topics, schedule)
```
No scheduler runs inside the app, so it still works if the host sleeps between pings.
Questions live in Postgres, not on the server disk, so redeploys can't wipe your queue.
Posts can be up to ~5 minutes later than the planned time (the ping interval).

## Setup (about 20 minutes)

### 1. Create the bot
In Telegram, talk to **@BotFather** → `/newbot` → copy the token.
Then open your bot and press **Start** (otherwise it can't send you alerts).

### 2. Create a free Postgres database
Any hosted Postgres works (e.g. Neon or Supabase — check their current free-tier terms).
Copy the connection string (`postgresql://...`). The bot creates its tables automatically.

### 3. Push this folder to GitHub
Never commit real secrets (`.env` is git-ignored).

### 4. Deploy on Ethiodeploy
Create a project from the GitHub repo. Set these **environment variables**:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | from BotFather |
| `DATABASE_URL` | Postgres connection string |
| `CRON_SECRET` | a long random string |
| `WEBHOOK_SECRET` | another long random string (letters, digits, `_`, `-`) |
| `PUBLIC_URL` | your deployed https URL, no trailing slash |

Start command (if it asks): `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
(a `Procfile` and a `Dockerfile` are included — use whichever the platform expects).
Generate secrets with: `python -c "import secrets; print(secrets.token_urlsafe(24))"`

### 5. Register the webhook (once)
Open `https://YOUR-URL/setup?key=CRON_SECRET` in a browser. You should see `"ok": true`.

### 6. Start the schedule pings
At **cron-job.org** (free) create a job: URL `https://YOUR-URL/tick?key=CRON_SECRET`,
every **5 minutes**. This is what posts the quizzes — if it stops, nothing is posted
(you'll get an alert the next time it runs).

### 7. Set up the group
1. Group settings → turn on **Topics**; create one topic per subject.
2. Add the bot as **admin** (it needs permission to post).
3. Inside **each** topic, send `/settopic <subject>` (e.g. `/settopic physics`) **as yourself,
   not as anonymous admin**. The bot confirms in that topic.
4. Check with `/topics` in a private chat with the bot.

### 8. Add questions
Private chat with the bot → `/template` → fill it in → send the `.xlsx` back.
The bot replies with how many were added and which rows were rejected and why.

| subject | question | A | B | C | D | correct | explanation |
|---------|----------|---|---|---|---|---------|-------------|
| math | What is 12 × 12? | 124 | 134 | 144 | 154 | C | 12 × 12 = 144 |

* Limits (Telegram): question 300 chars, option 100, explanation 200. C and D may be empty.
* Questions post in upload order (top row first). Re-uploading the same question text is ignored.
* Delete the `[SAMPLE]` rows from the template (they are skipped anyway).

## Admin commands (private chat with the bot)
`/queue` questions left per subject · `/today` today's post times · `/topics` topic mapping ·
`/template` Excel template · `/help`.
Add more admins with the `ADMIN_IDS` variable (comma-separated Telegram IDs).

## Alerts you will receive
* Queue empty / fewer than 2 questions left for a subject (once per day per subject)
* A subject has no topic set · Telegram refused to post · a quiz was skipped (service asleep or cron stopped)

## Behaviour worth knowing
* If a post fails because of setup (bot not admin, topic deleted) the question is **kept**; after 3 tries the
  slot is marked failed and you're alerted. If Telegram rejects the question itself, it's marked rejected and skipped.
* If a Telegram call times out and it's unclear whether the quiz posted, the bot does **not** retry
  (to avoid posting twice) — it tells you to check the topic.
* A quiz more than 2 hours late is skipped instead of posted at night.

## Tests
```
pip install -r requirements.txt pytest
TEST_DATABASE_URL=postgresql://user@localhost/testdb pytest -q   # WIPES that database's tables
```
