# Coding Conventions

**Analysis Date:** 2026-04-08

## Language & Runtime

**Primary:** Python 3.13 (inferred from `.venv/lib/python3.13`)

**Style enforcement:** No linter or formatter configuration detected (no `.flake8`, `.pylintrc`, `pyproject.toml`, `ruff.toml`, `mypy.ini`, or similar).

## Naming Patterns

**Files:**
- Use `snake_case.py` for all modules: `bot.py`, `ssh_manager.py`, `slurm_monitor.py`
- Flat structure (all files in project root, no packages/subdirectories)

**Classes:**
- `PascalCase`: `SSHManager`, `SlurmMonitor`, `JobInfo`
- Located in the module they primarily serve

**Functions:**
- `snake_case` for public methods: `get_active_jobs()`, `run_command()`, `cancel_job()`
- Leading underscore for private/internal: `_parse_squeue()`, `_get_completed_job()`, `_esc()`, `_poll_loop()`
- Command handlers prefixed with `cmd_`: `cmd_start()`, `cmd_status()`, `cmd_cancel()`

**Variables:**
- `snake_case` for all variables: `tracked_jobs`, `polling_task`, `slurm_user`
- Module-level globals for mutable state: `ssh`, `monitor`, `polling_task`, `tracked_jobs`, `pinned_jobs`

**Constants:**
- No dedicated constants file. Magic values are inline (e.g., `60` for poll interval, `30` for tail lines, `3800` for Telegram char limit, `50` for max history count)

## Code Style

**Formatting:**
- No formatter configured (no black, ruff, autopep8)
- 4-space indentation used consistently
- Double quotes for strings throughout
- Use f-strings for string interpolation exclusively

**Linting:**
- No linter configured
- Recommendation: Add `ruff` or `flake8` with a `pyproject.toml`

**Type Hints:**
- Modern union syntax used: `str | None`, `dict[str, str]`, `list[JobInfo]`
- Return types annotated on all public methods
- Parameter types annotated on all methods
- Uses `from __future__ import annotations` style implicitly (Python 3.13)
- No runtime type checking or validation library (no pydantic, attrs)

## Import Organization

**Order (observed in `bot.py`):**
1. Standard library (`asyncio`, `html`, `logging`, `os`, `signal`, `sys`)
2. Third-party (`dotenv`, `telegram`, `telegram.ext`)
3. Local modules (`ssh_manager`, `slurm_monitor`)

**Style:**
- Use `from module import Class` for specific imports
- Use grouped imports from the same package with parenthesized multi-line format
- Example from `bot.py`:
```python
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
```

## Data Modeling

**Pattern:** Use `@dataclass` for data objects.

- `JobInfo` in `slurm_monitor.py` is a `@dataclass` with computed properties (`@property`)
- Properties handle presentation logic: `state_emoji`, `label`, `format_short()`, `format_detail()`
- No NamedTuples, Pydantic models, or TypedDicts used

**When adding new data models:** Use `@dataclass` with `@property` for derived/computed fields. Keep formatting methods on the dataclass itself.

## Error Handling

**Patterns:**

1. **Command handlers** wrap all logic in `try/except Exception` and send error to user:
```python
try:
    result = monitor.get_history(count)
    await _reply(update, result)
except Exception as e:
    await update.message.reply_text(f"Error: {e}")
```

2. **SSH command failures** check return code and raise `RuntimeError`:
```python
if rc != 0:
    raise RuntimeError(f"squeue failed: {stderr}")
```

3. **Graceful degradation** for message formatting - HTML parse errors fall back to plain text:
```python
async def _reply(update, text):
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(text)
```

4. **Environment variable validation** uses fail-fast with `sys.exit(1)`:
```python
def get_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        logger.error(f"Missing env var: {key}")
        sys.exit(1)
    return value
```

5. **No custom exception classes** defined anywhere in the codebase.

## Logging

**Framework:** Standard library `logging` module

**Configuration** (in `bot.py`):
```python
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
```

**Patterns:**
- Use `logger.info()` for operational events (connection, startup)
- Use `logger.error()` for failures (SSH, env vars, poll errors)
- `SSHManager` uses `print()` instead of `logging` -- inconsistency
- f-strings used directly in log calls (not lazy `%s` formatting)

## Comments

**Section separators** use ASCII divider blocks:
```python
# ======================================================================
# Commands
# ======================================================================
```

**Docstrings:**
- All classes have brief docstrings
- Public methods have one-line docstrings describing purpose
- No parameter or return documentation (no Google/NumPy/Sphinx style)
- Private methods may lack docstrings

**Inline comments:**
- Used sparingly for non-obvious logic
- `# {job_id: job_name} for tracked jobs` style for data structure documentation

## Function Design

**Size:** Most functions are 10-30 lines. Largest is `_poll_loop()` at ~50 lines and `get_job_log()` at ~60 lines.

**Parameters:**
- Positional for required args
- Keyword with defaults for optional: `grep_pattern: str | None = None`, `tail_lines: int = 30`
- No `**kwargs` usage

**Return Values:**
- SSH operations return `tuple[str, str, int]` (stdout, stderr, returncode)
- Monitor methods return formatted HTML strings directly (presentation mixed with logic)
- `get_job_detail()` returns `JobInfo | None`

## Module Design

**Exports:** No `__all__` defined. Each module exposes its primary class.

**Barrel Files:** None. No `__init__.py` (flat structure, not a package).

**Module responsibilities:**
- `bot.py`: Telegram bot commands, polling loop, application entry point
- `ssh_manager.py`: SSH connection lifecycle and command execution
- `slurm_monitor.py`: Slurm command construction, output parsing, HTML formatting

## Authorization Pattern

All command handlers follow the same guard pattern:
```python
async def cmd_something(update, context):
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return
    # ... actual logic
```

Use walrus operator (`:=`) for SSH check pattern. Follow this pattern for all new commands.

## State Management

**Global mutable state** in `bot.py`:
- `ssh: SSHManager | None` -- SSH connection singleton
- `monitor: SlurmMonitor | None` -- monitor singleton
- `polling_task: asyncio.Task | None` -- background task reference
- `tracked_jobs: dict[str, str]` -- watched jobs (mutated in-place)
- `pinned_jobs: dict[str, str]` -- pinned jobs (mutated in-place)

All state is in-memory only. No persistence across restarts.

## Output Formatting

- All user-facing output uses Telegram HTML format (`<b>`, `<code>`, `<pre>`)
- `html.escape()` used via `_esc()` helper in `slurm_monitor.py` and directly in `bot.py`
- Emoji used extensively for visual status indicators
- Telegram message limit handled by truncating to ~3800 chars

---

*Convention analysis: 2026-04-08*
