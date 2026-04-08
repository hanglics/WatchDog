# Architecture

**Analysis Date:** 2026-04-08

## Pattern Overview

**Overall:** Three-layer monolith (Bot -> Monitor -> SSH)

**Key Characteristics:**
- Single-process Python application with three modules at root level
- Telegram bot as the user-facing interface, delegating to a Slurm monitor service
- SSH ControlMaster for persistent connection to an HPC cluster (Bunya at UQ)
- Async polling loop for background job state tracking
- No database; all state is in-memory (tracked jobs, pinned jobs)

## Layers

**Presentation Layer (Telegram Bot):**
- Purpose: Receives user commands via Telegram, formats and sends responses
- Location: `bot.py`
- Contains: Command handlers (`cmd_*` functions), authorization check, polling loop, message formatting helpers
- Depends on: `SlurmMonitor`, `SSHManager`, `python-telegram-bot` library
- Used by: End users via Telegram

**Business Logic Layer (Slurm Monitor):**
- Purpose: Queries Slurm workload manager, parses output, formats results as HTML
- Location: `slurm_monitor.py`
- Contains: `SlurmMonitor` class, `JobInfo` dataclass, time parsing/formatting utilities
- Depends on: `SSHManager`
- Used by: `bot.py` command handlers

**Infrastructure Layer (SSH Manager):**
- Purpose: Manages persistent SSH connection via ControlMaster for MFA-compatible sessions
- Location: `ssh_manager.py`
- Contains: `SSHManager` class with connect/disconnect/run_command methods
- Depends on: System `ssh` binary via `subprocess`
- Used by: `SlurmMonitor`

## Data Flow

**Command Request Flow:**

1. User sends Telegram command (e.g., `/status`)
2. `bot.py` handler receives `Update`, checks authorization via `is_authorized()`
3. Handler checks SSH connectivity via `_check_ssh()`
4. Handler calls appropriate `SlurmMonitor` method (e.g., `monitor.get_summary()`)
5. `SlurmMonitor` constructs Slurm CLI command string (squeue/sacct/scontrol)
6. `SlurmMonitor` calls `ssh.run_command(cmd)` which executes via SSH ControlMaster
7. `SlurmMonitor` parses pipe-delimited stdout into `JobInfo` dataclass instances
8. `SlurmMonitor` formats results as HTML string
9. Handler sends HTML reply to Telegram via `_reply()` or `_send()`

**Background Polling Flow (Watch):**

1. User sends `/watch` command
2. `cmd_watch()` populates `tracked_jobs` dict and creates `asyncio.Task` running `_poll_loop()`
3. `_poll_loop()` runs indefinitely, sleeping `POLL_INTERVAL` seconds between iterations
4. Each iteration queries `monitor.get_active_jobs()` to detect state changes and completions
5. State changes and completions trigger proactive Telegram messages via `_send()`
6. Loop exits when all tracked jobs complete or SSH connection drops

**State Management:**
- `ssh` (global): Single `SSHManager` instance, created at startup in `main()`
- `monitor` (global): Single `SlurmMonitor` instance, created at startup in `main()`
- `tracked_jobs` (global dict): `{job_id: job_name}` for jobs being watched by polling loop
- `pinned_jobs` (global dict): `{job_id: job_name}` for jobs pinned to always show in `/status`
- `polling_task` (global): Single `asyncio.Task` reference for the background poll loop
- All state is ephemeral -- lost on process restart

## Key Abstractions

**JobInfo:**
- Purpose: Represents a Slurm job with status, timing, and node information
- Location: `slurm_monitor.py` (lines 50-100)
- Pattern: Python `@dataclass` with computed properties (`is_running`, `is_pending`, `state_emoji`) and formatting methods (`format_short()`, `format_detail()`)

**SSHManager:**
- Purpose: Wraps SSH ControlMaster lifecycle and command execution
- Location: `ssh_manager.py`
- Pattern: Connection manager with `connect()`, `disconnect()`, `run_command()`, and `is_connected` property. Uses system `ssh` binary, not paramiko.

**SlurmMonitor:**
- Purpose: Encapsulates all Slurm command construction, execution, and output parsing
- Location: `slurm_monitor.py`
- Pattern: Service class that composes `SSHManager` for remote execution. Each public method maps to one or more Slurm commands (squeue, sacct, scontrol, sinfo, sshare, sprio, scancel).

## Entry Points

**Main Entry Point:**
- Location: `bot.py`, `main()` function (line 552)
- Triggers: `python bot.py` (guarded by `if __name__ == "__main__":`)
- Responsibilities:
  1. Load environment variables from `.env`
  2. Establish SSH connection (interactive MFA prompt)
  3. Verify Slurm connectivity with a test `squeue` call
  4. Register all Telegram command handlers
  5. Set Telegram command menu via `post_init` callback
  6. Register SIGINT/SIGTERM handlers for graceful shutdown
  7. Start Telegram polling loop (`app.run_polling()`)

## Error Handling

**Strategy:** Try/except at command handler level with user-facing error messages

**Patterns:**
- Every `cmd_*` handler wraps its core logic in `try/except Exception` and sends `"Error: {e}"` to the user
- `_reply()` attempts HTML parse mode first, falls back to plain text on failure
- `_send()` (for background notifications) uses the same HTML-then-plain fallback
- SSH connection loss is detected in the poll loop via `ssh.is_connected` check, triggering loop exit
- `SlurmMonitor` raises `RuntimeError` on squeue failures; returns fallback strings for empty results
- `SSHManager.run_command()` propagates `subprocess.TimeoutExpired` if command exceeds timeout
- No retry logic exists for transient SSH or Slurm failures

## Cross-Cutting Concerns

**Logging:** Python `logging` module, configured at INFO level in `bot.py` with format `%(asctime)s [%(levelname)s] %(message)s`. Only `bot.py` uses the logger; `ssh_manager.py` uses `print()` directly.

**Validation:** Minimal. Command arguments checked for `.isdigit()` before use. No schema validation. Authorization is a simple chat ID string comparison.

**Authentication:** Two layers:
1. Telegram: Single authorized chat ID checked via `is_authorized()` comparing `update.effective_chat.id` against `TELEGRAM_CHAT_ID` env var
2. SSH: Interactive password + MFA at startup, then persistent ControlMaster socket

**Configuration:** All via environment variables loaded from `.env` file using `python-dotenv`. Required: `TELEGRAM_BOT_TOKEN`, `BUNYA_USER`. Optional with defaults: `BUNYA_HOST` (defaults to `bunya.rcc.uq.edu.au`), `POLL_INTERVAL` (defaults to `60`), `TELEGRAM_CHAT_ID`.

---

*Architecture analysis: 2026-04-08*
