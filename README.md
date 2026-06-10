# ASD VPS Panel

Matrix-themed web VPS control panel. Deploy bots and scripts from your browser.

## Features
- Login with username/password
- Create & manage multiple projects
- Upload .py / .js / .zip files
- Install pip packages
- Start/Stop processes with live logs
- Built-in terminal/console
- Real-time log streaming via Socket.IO

## Deploy on Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Set these environment variables in Railway dashboard:

| Variable | Description | Default |
|---|---|---|
| `VPS_USERNAME` | Login username | `admin` |
| `VPS_PASSWORD` | Login password | `admin123` |
| `SECRET_KEY` | Flask session secret | random string |

4. Railway auto-detects `Procfile` and runs `python app.py`

## File Structure
```
vps-panel/
├── app.py              ← Flask backend (main file)
├── requirements.txt    ← Python dependencies
├── Procfile            ← Railway start command
├── templates/
│   ├── login.html      ← Login page
│   └── index.html      ← Dashboard
└── projects/           ← Auto-created, stores your projects
```

## Usage
1. Open your Railway URL
2. Login with your credentials
3. Click **+** to create a new project
4. Upload your bot file (.py or .zip)
5. Set main file in Settings tab
6. Click **Start** — logs appear in Logs tab

## Important Notes
- Projects run on the Railway server itself
- Telegram bots, Discord bots etc. will run as subprocesses
- `pip install` works from the Packages tab
- Use Console tab for direct terminal access
