# CYBERTON Telegram Bot

Async Telegram bot (python-telegram-bot, long polling) with a health-check
endpoint, built for Railway.

## Commands
/start, /help, /ping, /status, /about

## Run locally (Windows PowerShell)
```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then put your real token in .env
python main.py
```
Health check: http://localhost:8080/health

## Deploy on Railway
1. Push this project to a GitHub repository.
2. Railway: New Project -> Deploy from GitHub repo -> select the repo.
3. Service -> Variables -> add `TELEGRAM_BOT_TOKEN` (from @BotFather).
4. `railway.json` configures the start command, health check (`/health`)
   and restart policy. Every `git push` to the connected branch redeploys.

## Notes
- Run exactly ONE instance per token (stop local runs when deployed).
- Never commit `.env` or share your token.
