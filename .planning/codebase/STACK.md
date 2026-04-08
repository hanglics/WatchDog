# Technology Stack

**Analysis Date:** 2026-04-08

## Languages

**Primary:**
- Python 3.12+ - All application code (`bot.py`, `slurm_monitor.py`, `ssh_manager.py`)

**Secondary:**
- None

## Runtime

**Environment:**
- CPython 3.12+ (uses `str | None` union syntax, `match :=` walrus operator)

**Package Manager:**
- pip
- Lockfile: missing (only `requirements.txt` with loose version pins)

## Frameworks

**Core:**
- python-telegram-bot >=21.0 - Async Telegram Bot API framework (v21+ uses `Application` builder pattern)

**Testing:**
- Not detected - no test framework configured

**Build/Dev:**
- python-dotenv >=1.0.0 - Environment variable loading from `.env` files

## Key Dependencies

**Critical:**
- `python-telegram-bot` >=21.0 - Entire bot interface; uses `Application`, `CommandHandler`, `ContextTypes` from `telegram.ext`
- `python-dotenv` >=1.0.0 - Loads `.env` config at startup via `load_dotenv()`

**Infrastructure:**
- `subprocess` (stdlib) - Executes all SSH commands via `subprocess.run()` in `ssh_manager.py`
- `asyncio` (stdlib) - Powers the polling loop and Telegram bot event loop in `bot.py`
- `dataclasses` (stdlib) - `JobInfo` data model in `slurm_monitor.py`

## Configuration

**Environment:**
- Configured via `.env` file loaded by `python-dotenv`
- `.env.example` present as template
- `.env` file present (contents not read - contains secrets)
- Required env vars:
  - `TELEGRAM_BOT_TOKEN` - Bot authentication token
  - `TELEGRAM_CHAT_ID` - Authorized chat ID for access control
  - `BUNYA_USER` - HPC cluster username
- Optional env vars:
  - `BUNYA_HOST` - HPC hostname (default: `bunya.rcc.uq.edu.au`)
  - `POLL_INTERVAL` - Polling interval in seconds (default: `60`)

**Build:**
- No build configuration - runs directly as `python bot.py`
- Virtual environment setup: `python -m venv .venv`

## Platform Requirements

**Development:**
- Python 3.12+
- SSH client installed (uses system `ssh` binary)
- SSH access to target HPC cluster with password + MFA
- Telegram account and bot token from @BotFather

**Production:**
- Runs on developer's local machine (laptop) - not deployed to a server
- Requires persistent terminal session for initial SSH MFA authentication
- SSH ControlMaster keeps connection alive in background

---

*Stack analysis: 2026-04-08*
