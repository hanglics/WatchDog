# Bunya Monitor

A Telegram bot that monitors Slurm jobs on an HPC cluster over a persistent SSH connection.

## Why

HPC clusters with MFA-protected SSH make it tedious to repeatedly check job status. Bunya Monitor authenticates once, keeps the SSH session alive, and lets you query and control jobs from Telegram on your phone.

## How It Works

```
Your phone (Telegram)  <-->  bot.py (local laptop)  <-->  Bunya HPC (SSH)
                                    |
                          SSH ControlMaster
                         (single MFA login)
```

1. You start `bot.py` on your laptop and authenticate once (password + MFA)
2. SSH ControlMaster keeps the connection alive in the background
3. All Slurm commands (`squeue`, `sacct`, `scancel`, `scontrol`) run over that connection
4. The Telegram bot receives your commands and returns formatted results

## Setup

### 1. Create a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow the prompts, and copy the **bot token**
3. Message your new bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Find your `chat_id` from the JSON response

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```
BUNYA_USER=your_username
BUNYA_HOST=bunya.rcc.uq.edu.au
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
POLL_INTERVAL=60
```

### 3. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Run

```bash
python bot.py
```

You will be prompted for your password and MFA code in the terminal. After authentication, the SSH connection persists in the background and the bot starts listening.

## Commands

### Status

| Command | Description |
|---------|-------------|
| `/status` | All active jobs with running/pending counts |
| `/status <job_id>` | Detailed info for a specific job |
| `/eta <job_id>` | Time used vs time limit, max remaining time |
| `/queue` | Cluster-wide queue: total jobs, GPU partition info, your queue position |
| `/fairshare` | Your fairshare score and pending job priorities |
| `/summary` | Today's digest: completed, failed, cancelled counts and total compute time |
| `/history [N]` | Last N completed jobs with state and exit codes (default 10) |
| `/failed` | Failed/cancelled/timed-out jobs from the last 7 days |

### Logs

| Command | Description |
|---------|-------------|
| `/log <job_id>` | Tail last 30 lines of the job's stdout |
| `/log <job_id> <keyword>` | Grep the log for a keyword (case-insensitive) |
| `/output <job_id>` | Show StdOut, StdErr, and WorkDir paths |

### Control

| Command | Description |
|---------|-------------|
| `/cancel <job_id>` | Cancel a specific job |
| `/cancel all` | Cancel all your jobs |
| `/watch` | Watch all active jobs; notify on state changes and completion |
| `/watch <id1,id2,...>` | Watch specific jobs only |
| `/watch list` | Show currently watched jobs |
| `/stop all` | Stop watching all jobs |
| `/stop <id1,id2,...>` | Stop watching specific jobs |
| `/pin <job_id>` | Pin a job so it always appears in `/status` even after finishing |
| `/pin` | List all pinned jobs |
| `/unpin <job_id>` | Unpin a job |
| `/unpin all` | Clear all pins |
| `/ssh` | Check SSH connection status |
| `/help` | Show all commands |

### Watch Mode

`/watch` is the main fire-and-forget feature:

1. Submit your jobs to the cluster
2. Send `/watch` (or `/watch 123,456,789` for specific jobs)
3. The bot polls every `POLL_INTERVAL` seconds and sends you notifications:
   - State changes: `PENDING -> RUNNING`
   - Completions: `COMPLETED` or `FAILED` with exit info
   - Final message when all tracked jobs are done

## Project Structure

```
bunya-monitor/
├── bot.py              # Telegram bot: command handlers and poll loop
├── slurm_monitor.py    # Slurm query layer: squeue/sacct/scancel parsing
├── ssh_manager.py      # SSH ControlMaster connection manager
├── requirements.txt    # Python dependencies
├── .env.example        # Configuration template
└── .gitignore
```

## Requirements

- Python 3.12+
- SSH access to the HPC cluster
- A Telegram account and bot token
