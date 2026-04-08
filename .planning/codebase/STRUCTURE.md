# Codebase Structure

**Analysis Date:** 2026-04-08

## Directory Layout

```
bunya-monitor/
├── bot.py              # Telegram bot: commands, polling, main entry point
├── slurm_monitor.py    # Slurm query/parse logic and JobInfo dataclass
├── ssh_manager.py      # SSH ControlMaster connection manager
├── requirements.txt    # Python dependencies
├── .env                # Environment configuration (secrets, not committed)
├── .env.example        # Template for required env vars
├── .gitignore          # Ignores .env, __pycache__, .venv
├── README.md           # Project documentation
├── .venv/              # Python virtual environment (not committed)
├── __pycache__/        # Python bytecode cache (not committed)
├── .ruff_cache/        # Ruff linter cache (not committed)
└── .planning/          # Planning documents
    └── codebase/       # Architecture analysis docs
```

## Directory Purposes

**Root (`/`):**
- Purpose: All application source code lives at root level (flat structure)
- Contains: 3 Python source files, config files, documentation
- Key files: `bot.py`, `slurm_monitor.py`, `ssh_manager.py`

**.venv/:**
- Purpose: Python 3.13 virtual environment
- Generated: Yes
- Committed: No

**.ruff_cache/:**
- Purpose: Ruff linter/formatter cache (v0.15.6)
- Generated: Yes
- Committed: No

## Key File Locations

**Entry Points:**
- `bot.py`: Main application entry point (`python bot.py`)

**Configuration:**
- `.env`: Runtime configuration (secrets -- never read contents)
- `.env.example`: Template showing required/optional env vars
- `requirements.txt`: Python package dependencies

**Core Logic:**
- `slurm_monitor.py`: All Slurm interaction logic (`SlurmMonitor` class, `JobInfo` dataclass)
- `ssh_manager.py`: SSH connection management (`SSHManager` class)
- `bot.py`: Telegram command handlers and background polling

**Testing:**
- No test files exist in the codebase

## Naming Conventions

**Files:**
- `snake_case.py`: All Python source files use snake_case

**Classes:**
- `PascalCase`: `SSHManager`, `SlurmMonitor`, `JobInfo`

**Functions:**
- `snake_case`: Public methods like `get_active_jobs()`, `run_command()`
- `_prefixed_snake_case`: Private/internal functions like `_check_ssh()`, `_poll_loop()`, `_parse_squeue()`
- `cmd_` prefix: Telegram command handlers like `cmd_start()`, `cmd_status()`

**Variables:**
- `snake_case`: `tracked_jobs`, `polling_task`, `slurm_user`
- `UPPER_CASE`: Environment variable names like `TELEGRAM_BOT_TOKEN`, `POLL_INTERVAL`

## Where to Add New Code

**New Telegram Command:**
1. Add `async def cmd_<name>(update, context)` function in `bot.py` (in the Commands section, ~line 82-353)
2. Add entry to the `handlers` dict in `main()` at `bot.py` line 575
3. Add command description to `post_init()` at `bot.py` line 598
4. Add help text to `cmd_start()` at `bot.py` line 83

**New Slurm Query:**
1. Add method to `SlurmMonitor` class in `slurm_monitor.py`
2. Follow existing pattern: construct Slurm command string, call `self.ssh.run_command()`, parse stdout, return formatted HTML string
3. Group with related methods using comment section headers (`# ---`)

**New SSH Capability:**
1. Add method to `SSHManager` class in `ssh_manager.py`

**New Data Model:**
1. Add `@dataclass` in `slurm_monitor.py` alongside `JobInfo`

**New Module (if codebase grows):**
1. Create `<module_name>.py` at root level
2. Import in `bot.py` or `slurm_monitor.py` as needed
3. Consider creating a `src/` package if file count exceeds ~8-10 files

**Utilities:**
- Time parsing helpers are in `slurm_monitor.py` (module-level functions `_parse_time_seconds()`, `_format_duration()`)
- HTML escaping helper `_esc()` is in `slurm_monitor.py`
- Bot messaging helpers `_reply()` and `_send()` are in `bot.py`

## Special Directories

**`.venv/`:**
- Purpose: Python 3.13 virtual environment with installed packages
- Generated: Yes (via `python -m venv .venv`)
- Committed: No

**`__pycache__/`:**
- Purpose: Python compiled bytecode
- Generated: Yes (automatic)
- Committed: No

**`.ruff_cache/`:**
- Purpose: Ruff linter cache
- Generated: Yes (by ruff)
- Committed: No (not in .gitignore but should be)

**`~/.ssh/bunya-monitor/`:**
- Purpose: SSH ControlMaster socket files (created at runtime by `SSHManager`)
- Location: User home directory, outside project
- Generated: Yes
- Committed: N/A

---

*Structure analysis: 2026-04-08*
