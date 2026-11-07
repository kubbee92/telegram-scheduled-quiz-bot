# Telegram Academic Quiz Bot

Posts anonymous quiz-mode polls (one correct answer + short explanation) to a Telegram group using **Telegram Topics**. It sends **2 quizzes per day** at random evening times (18:30–21:00 Addis Ababa). Admins add questions seamlessly by uploading an Excel file directly to the bot.

---

## How It Works

* **Stateless App Architecture:** No background scheduler runs inside the app process. Instead, an external ping service hits `/tick` every 5 minutes. The app plans the day's random times on the fly and posts whatever is due.
* **Webhook Handling:** Telegram updates (Excel uploads, admin configurations like `/settopic`) hit `/webhook`.
* **Database Backend:** All questions, active topics, and queue states live strictly in **PostgreSQL**, meaning redeploys will never wipe your queue.

---

## Setup Guide

### 1. Create the Bot
1. Message **@BotFather** on Telegram to create your bot and copy your `BOT_TOKEN`.
2. Open a direct chat with your new bot and press **Start** so it has permission to message you.

### 2. Set Up the Database
1. Create a hosted PostgreSQL database (e.g., via Neon, Supabase, or Render).
2. Copy your connection string (`postgresql://...`). The app initializes its required tables automatically on startup.

### 3. Deploy
Deploy the repository to your host of choice (such as Ethiodeploy or Render) and set the following environment variables:

| Variable | Description |
| :--- | :--- |
| `BOT_TOKEN` | Provided by BotFather |
| `DATABASE_URL` | Your Postgres connection string |
| `CRON_SECRET` | A secure random string for protecting the tick endpoint |
| `WEBHOOK_SECRET` | A secure random string for protecting webhooks |
| `PUBLIC_URL` | Your deployed application's HTTPS URL (no trailing slash) |
| `ADMIN_IDS` | Comma-separated list of authorized Telegram user IDs |

### 4. Register the Webhook
Open `https://YOUR-PUBLIC-URL/setup?key=CRON_SECRET` once in your browser. You should receive a response confirming `{"ok": true}`.

### 5. Configure the Cron Pinger
Set up an external cron monitor (such as cron-job.org) to send a GET request every **5 minutes** to:
`https://YOUR-PUBLIC-URL/tick?key=CRON_SECRET`

### 6. Configure Telegram Topics
1. Enable **Topics** in your Telegram group settings and create one topic for each subject.
2. Add your bot to the group as an **Administrator** with permission to send messages.
3. Inside *each* specific topic, send `/settopic <subject>` (e.g., `/settopic physics`) **as yourself** (not as an anonymous admin). The bot will reply inside that topic to confirm the binding.

---

## Adding Questions

1. Open a private chat with the bot and send `/template` to download the standard Excel template.
2. Fill out your questions following this format:

| subject | question | A | B | C | D | correct | explanation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| math | What is 12 × 12? | 124 | 134 | 144 | 154 | C | 12 × 12 = 144 |

3. Send the completed `.xlsx` file back to the bot in your private chat. The bot will parse the file, add valid rows to the queue, and report any rejections.

---

## Admin Commands (Private Chat)

* `/queue` — View remaining questions left per subject.
* `/today` — Check scheduled post times for today.
* `/topics` — View current topic-to-subject mappings.
* `/template` — Download the questions Excel template.
* `/help` — View available commands.
