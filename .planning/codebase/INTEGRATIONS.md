# External Integrations

**Analysis Date:** 2026-04-08

## APIs & External Services

**Telegram Bot API:**
- Purpose: User interface for all bot commands and notifications
- SDK/Client: `python-telegram-bot` >=21.0 (`telegram.ext.Application`)
- Auth: `TELEGRAM_BOT_TOKEN` env var
- Entry point: `bot.py` line 573 (`Application.builder().token(token).build()`)
- Features used:
  - Command handlers (16 commands registered)
  - HTML parse mode for formatted messages
  - Bot command menu registration via `set_my_commands()`
  - Long polling via `app.run_polling()`
  - Async message sending via `bot.send_message()`
- Access control: Chat ID whitelist via `TELEGRAM_CHAT_ID` env var, checked in `is_authorized()` at `bot.py` line 51

**Slurm HPC Workload Manager:**
- Purpose: Job scheduling, monitoring, and control on the Bunya HPC cluster
- Client: Shell commands executed over SSH (`slurm_monitor.py`)
- Auth: SSH with password + MFA (one-time interactive authentication)
- Commands used:
  - `squeue` - Active job listing and details
  - `sacct` - Historical job accounting (completed, failed jobs)
  - `scancel` - Job cancellation
  - `scontrol` - Job metadata (stdout paths, working directories)
  - `sinfo` - Partition/node information (GPU partition)
  - `sshare` - Fairshare/priority information
  - `sprio` - Pending job priority details

## Data Storage

**Databases:**
- None - all state is in-memory

**In-Memory State (`bot.py`):**
- `tracked_jobs: dict[str, str]` - Currently watched jobs (line 34)
- `pinned_jobs: dict[str, str]` - Pinned jobs for persistent status display (line 36)
- `polling_task: asyncio.Task` - Background polling coroutine reference (line 33)
- State is lost on restart

**File Storage:**
- Local filesystem only
- SSH control socket: `~/.ssh/bunya-monitor/ctrl-%r@%h:%p` (`ssh_manager.py` line 20-21)

**Caching:**
- None - every command queries Slurm in real-time over SSH

## Authentication & Identity

**SSH Authentication:**
- Implementation: SSH ControlMaster with persistent session (`ssh_manager.py`)
- Flow:
  1. Initial connection requires interactive password + MFA (`ssh_manager.py` line 46-57)
  2. ControlMaster keeps session alive with `ControlPersist=yes`
  3. Subsequent commands reuse the authenticated socket (`ControlMaster=no`)
  4. Keepalive: `ServerAliveInterval=60`, `ServerAliveCountMax=10`
- Control socket path: `~/.ssh/bunya-monitor/ctrl-%r@%h:%p`
- Connection check: `ssh -O check` (`ssh_manager.py` line 25-34)
- Disconnect: `ssh -O exit` (`ssh_manager.py` line 92-104)

**Telegram Authorization:**
- Implementation: Simple chat ID whitelist (single user)
- Config: `TELEGRAM_CHAT_ID` env var
- Check: `is_authorized()` in `bot.py` line 51-54
- Every command handler checks authorization before proceeding

## Monitoring & Observability

**Error Tracking:**
- None - no external error tracking service

**Logs:**
- Python `logging` module with `INFO` level (`bot.py` line 23-27)
- Format: `%(asctime)s [%(levelname)s] %(message)s`
- Output: stdout only (no log files, no log rotation)
- SSH manager uses `print()` statements instead of logging (`ssh_manager.py`)

## CI/CD & Deployment

**Hosting:**
- Local machine (developer's laptop) - no cloud deployment
- Started manually: `python bot.py`

**CI Pipeline:**
- None - no CI/CD configuration detected

## Environment Configuration

**Required env vars:**
- `TELEGRAM_BOT_TOKEN` - Telegram Bot API token from @BotFather
- `TELEGRAM_CHAT_ID` - Numeric chat ID for the authorized user
- `BUNYA_USER` - Username for SSH connection to HPC cluster

**Optional env vars:**
- `BUNYA_HOST` - HPC hostname (default: `bunya.rcc.uq.edu.au`)
- `POLL_INTERVAL` - Background polling interval in seconds (default: `60`)

**Secrets location:**
- `.env` file in project root (git-ignored)
- `.env.example` provides template with placeholder values

## Webhooks & Callbacks

**Incoming:**
- None - bot uses long polling, not webhooks

**Outgoing:**
- None

---

*Integration audit: 2026-04-08*
