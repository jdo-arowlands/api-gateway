# Railway Deployment Guide

## Project Structure (Railway root)
```
/
├── main.py              ← FastAPI app entry point
├── database.py          ← SQLAlchemy models (auto-switches SQLite ↔ Postgres)
├── scheduler.py         ← APScheduler job engine
├── api_caller.py        ← Authenticated HTTP client + logging
├── token_manager.py     ← OAuth2/Bearer token lifecycle
├── webhooks.py          ← Inbound webhook receivers
├── actions.py           ← Your job action functions (edit this)
├── requirements.txt     ← Python dependencies
├── Procfile             ← Process definition
├── railway.toml         ← Railway build/deploy config
├── .env.example         ← Copy → .env for local dev
└── static/
    └── index.html       ← Dashboard UI
```

---

## Step 1 — Push to GitHub

```bash
cd api-gateway-railway
git init
git add .
git commit -m "Initial API Gateway"
git remote add origin https://github.com/YOUR_USERNAME/api-gateway.git
git push -u origin main
```

---

## Step 2 — Create Railway Project

1. Go to [railway.app](https://railway.app) → **New Project**
2. Choose **Deploy from GitHub repo** → select your repo
3. Railway auto-detects Python via Nixpacks — no Dockerfile needed

---

## Step 3 — Add PostgreSQL

1. In your Railway project dashboard click **+ New**
2. Select **Database → PostgreSQL**
3. Railway automatically sets `DATABASE_URL` in your service's environment
   - The app reads this on startup and connects with SSL

---

## Step 4 — Set Environment Variables

In your Railway service → **Variables** tab, add:

| Variable            | Value                            | Notes                          |
|---------------------|----------------------------------|--------------------------------|
| `DATABASE_URL`      | *(auto-set by Postgres plugin)*  | Do NOT change this             |
| `SECRET_KEY`        | `<random 40-char string>`        | Used for HMAC webhook signing  |
| `DASHBOARD_USER`    | `admin`                          | Dashboard login username       |
| `DASHBOARD_PASSWORD`| `<strong password>`              | Dashboard login password       |
| `LOG_LEVEL`         | `INFO`                           | Optional                       |

Generate a SECRET_KEY:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Step 5 — Deploy

Railway deploys automatically on every push to `main`.

Watch the build log — you should see:
```
✓ Nixpacks build
✓ pip install -r requirements.txt
✓ uvicorn main:app --host 0.0.0.0 --port $PORT
```

Health check hits `/health` → `{"status": "ok"}` → service goes green.

---

## Step 6 — Access Your Dashboard

Railway provides a public URL like:
```
https://api-gateway-production-xxxx.up.railway.app
```

Open it — you'll be prompted for your `DASHBOARD_USER` / `DASHBOARD_PASSWORD`.

---

## Step 7 — Configure Retell AI Webhook

In Retell dashboard → **Settings → Webhooks**:

```
https://api-gateway-production-xxxx.up.railway.app/webhooks/retell
```

Events map automatically:
- `call_ended`    → job `retell_call_ended`
- `call_analyzed` → job `retell_call_analyzed`
- `call_started`  → job `retell_call_started`

Make sure you've created those jobs in the dashboard (type: **webhook**).

---

## Step 8 — Create Your First Jobs

Via the dashboard or curl:

```bash
BASE=https://your-app.up.railway.app

# Webhook job — fires when Retell posts call_ended
curl -X POST $BASE/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "retell_call_ended",
    "job_type": "webhook",
    "action": "retell_call_ended",
    "description": "Handle Retell call ended events"
  }'

# Scheduled job — token refresh every hour
curl -X POST $BASE/api/jobs \
  -d '{
    "name": "token_refresh_hourly",
    "job_type": "interval",
    "schedule": "1h",
    "action": "token_refresh_all",
    "description": "Keep all bearer tokens fresh"
  }'

# Cron job — daily health check at 8am UTC
curl -X POST $BASE/api/jobs \
  -d '{
    "name": "daily_health_check",
    "job_type": "cron",
    "schedule": "0 8 * * *",
    "action": "health_check_all"
  }'
```

---

## Local Development

```bash
cp .env.example .env
# edit .env — DATABASE_URL defaults to SQLite, no Postgres needed locally

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# Dashboard: http://localhost:8000
```

---

## Re-deploying

```bash
git add .
git commit -m "Update actions"
git push
# Railway auto-redeploys in ~60 seconds
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `could not connect to server` | Check Postgres plugin is added and `DATABASE_URL` is set |
| `SSL connection required` | Already handled — `sslmode=require` is set in `database.py` |
| Dashboard returns 401 | Check `DASHBOARD_USER` / `DASHBOARD_PASSWORD` env vars |
| Jobs not running | Check APScheduler logs — timezone is UTC |
| Retell webhook 404 | Confirm job name exactly matches `RETELL_EVENTS_TO_JOBS` map in `webhooks.py` |
| Build fails | Check Railway build log; most common cause is a missing dep in `requirements.txt` |
