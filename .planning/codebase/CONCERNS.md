# Codebase Concerns

**Analysis Date:** 2026-04-08

## Tech Debt

**Global mutable state in `bot.py`:**
- Issue: All application state (`ssh`, `monitor`, `polling_task`, `tracked_jobs`, `pinned_jobs`) is stored in module-level global variables mutated via `global` keyword. This makes the code untestable, prevents running multiple bot instances, and creates hidden coupling between functions.
- Files: `bot.py` (lines 29-36, 388, 489, 502-503, 533-534, 553)
- Impact: Cannot write unit tests for command handlers without monkeypatching globals. Cannot run integration tests in parallel. Refactoring any command handler requires understanding all shared state.
- Fix approach: Create a `BotContext` or `BotState` dataclass holding `ssh`, `monitor`, `tracked_jobs`, `pinned_jobs`, and pass it via `context.bot_data` (python-telegram-bot's built-in mechanism for shared state). This eliminates all `global` declarations.

**No separation between bot commands and business logic:**
- Issue: Command handlers in `bot.py` directly call `monitor` methods, format responses, and manage state all in one place. There is no service layer.
- Files: `bot.py` (all `cmd_*` functions)
- Impact: Adding a new interface (e.g., Discord bot, web UI, CLI) requires duplicating all business logic. Testing command logic requires mocking Telegram's `Update` object.
- Fix approach: Extract a `BotService` class that encapsulates watch/pin/status logic, with `bot.py` handlers being thin wrappers that call service methods and send responses.

**Duplicated error handling pattern:**
- Issue: Every command handler repeats the same try/except + authorization + SSH check boilerplate (authorization check, SSH check, try/except with error reply). This is ~8 lines duplicated across 14 handlers.
- Files: `bot.py` (lines 83-150, 153-175, 178-193, 196-207, 210-225, 228-239, 242-253, 256-275, 278-293, 296-307, 310-334, 337-352, 369-425, 482-537, 540-545)
- Impact: Adding a new command requires copying the boilerplate. Changing error handling (e.g., adding logging) requires modifying every handler.
- Fix approach: Create a decorator `@authorized_command` that wraps authorization check, SSH check, and try/except error handling. Each handler then contains only its unique logic.

**Hardcoded GPU partition name:**
- Issue: The `get_queue_info` method hardcodes `gpu_cuda` as the GPU partition name, which is specific to the Bunya cluster.
- Files: `slurm_monitor.py` (line 262)
- Impact: Breaks if the partition is renamed or if the tool is used on a different cluster.
- Fix approach: Make GPU partition name configurable via environment variable or constructor parameter.

## Known Bugs

**HTML parsing fragility in pinned jobs detection:**
- Symptoms: The pinned jobs feature in `/status` uses string parsing of HTML output to detect which job IDs are already shown in the active summary, using `split("</code>")[0].split(">")[-1]`. This breaks if the HTML structure of `format_short()` changes.
- Files: `bot.py` (line 136)
- Trigger: Any change to `JobInfo.format_short()` or `JobInfo.label` that alters the HTML tag structure.
- Workaround: None. The code works with the current format but is fragile.
- Fix: Have `get_summary()` return both the formatted string and a set of job IDs, or compare against `get_active_jobs()` directly (which is already called inside `get_summary()`).

**`_poll_loop` continues after SSH reconnect is impossible:**
- Symptoms: If SSH drops, the poll loop sends one warning and breaks, but `polling_task` is never set to `None` and `tracked_jobs` is not cleared. Subsequent `/watch` calls will see `polling_task.done() == True` and allow a new watch, but the old tracked_jobs state persists.
- Files: `bot.py` (lines 436-438, 389)
- Trigger: SSH connection drops during active polling.
- Workaround: User must run `/stop all` and then `/watch` again.

**No timeout exception handling in `run_command`:**
- Symptoms: `subprocess.run` with `timeout` raises `subprocess.TimeoutExpired`, but this exception is never caught in `SSHManager.run_command`. It propagates up to the bot command handler where it is caught as a generic `Exception`, resulting in an unhelpful error message.
- Files: `ssh_manager.py` (lines 73-90)
- Trigger: Any Slurm command taking longer than the timeout (default 60s).
- Workaround: The generic catch in bot handlers prevents a crash, but the error message is not user-friendly.

## Security Considerations

**Command injection via user-supplied grep pattern:**
- Risk: The `/log <job_id> <keyword>` command passes user input into a shell command executed on the remote server. While single quotes are escaped (`replace("'", "'\\''")` at `slurm_monitor.py` line 378), the pattern is interpolated into a `grep -i '{safe_pattern}'` command. An attacker with access to the Telegram chat could potentially craft input to escape the single-quote context.
- Files: `slurm_monitor.py` (lines 377-379)
- Current mitigation: Single-quote escaping, and the bot is restricted to a single authorized `TELEGRAM_CHAT_ID`.
- Recommendations: Use `shlex.quote()` for proper shell escaping instead of manual replacement. Alternatively, validate the grep pattern against a whitelist of safe characters (alphanumeric, spaces, dots, dashes).

**Command injection via job_id parameter:**
- Risk: Job IDs are checked with `.isdigit()` before use, which prevents injection in most commands. However, `cancel_job` and `cancel_all_jobs` directly interpolate values into `scancel` commands. The `.isdigit()` check in bot.py is the only guard.
- Files: `slurm_monitor.py` (lines 146, 153), `bot.py` (lines 168, 126, 264, 285, 317)
- Current mitigation: `.isdigit()` validation in bot.py before calling monitor methods.
- Recommendations: Add input validation inside `SlurmMonitor` methods as defense in depth. The monitor should not trust that callers validated inputs.

**Single chat ID authorization model:**
- Risk: Authorization is based on a single `TELEGRAM_CHAT_ID` environment variable compared as a string. There is no rate limiting, no audit logging of commands, and no multi-user support.
- Files: `bot.py` (lines 51-54)
- Current mitigation: Single authorized chat ID.
- Recommendations: For a personal tool this is acceptable. If shared, add: support for multiple authorized chat IDs, command audit logging, rate limiting on destructive commands (`/cancel`).

**`.env` file present in working directory:**
- Risk: `.env` file exists and is listed in `.gitignore`, which is correct. However, there is no validation that `.env` is not accidentally committed.
- Files: `.env`, `.gitignore`
- Current mitigation: `.gitignore` includes `.env`.
- Recommendations: Add a pre-commit hook to prevent `.env` from being committed.

## Performance Bottlenecks

**Sequential SSH commands in `get_queue_info`:**
- Problem: `get_queue_info()` executes 5 separate SSH commands sequentially, each with up to 30s timeout.
- Files: `slurm_monitor.py` (lines 250-275)
- Cause: Each `run_command` call opens a new SSH channel (though via ControlMaster, the overhead is lower than a full connection). Still, 5 sequential round-trips add latency.
- Improvement path: Combine all 5 commands into a single SSH command using `&&` or `;` separators, parsing the combined output. This reduces 5 round-trips to 1.

**`get_daily_summary` makes redundant `get_active_jobs` call:**
- Problem: `get_daily_summary()` calls `self.get_active_jobs()` internally, but the caller in `bot.py` does not cache this. If the user calls `/status` followed by `/summary`, active jobs are fetched twice.
- Files: `slurm_monitor.py` (lines 445-446)
- Cause: No caching of Slurm query results.
- Improvement path: Add a short-lived cache (e.g., 5-10 seconds TTL) for `get_active_jobs()` results to avoid redundant SSH queries during rapid command sequences.

**`is_connected` property triggers a subprocess on every access:**
- Problem: `is_connected` runs `ssh -O check` as a subprocess every time it is accessed. In the poll loop, this is called every interval, which is fine. But in other code paths, multiple property accesses could occur.
- Files: `ssh_manager.py` (lines 24-34)
- Cause: No caching of connection status.
- Improvement path: Cache the result for a few seconds, or check only when needed.

## Fragile Areas

**SSH ControlMaster dependency:**
- Files: `ssh_manager.py` (entire file)
- Why fragile: The entire application depends on SSH ControlMaster working correctly. If the control socket file is deleted, or the background SSH process crashes, or the server reboots, the application silently loses its connection. The only detection is the `is_connected` check, which is not called proactively.
- Safe modification: Always test SSH reconnection scenarios. The `connect()` method can be called again, but the bot has no automatic reconnection logic.
- Test coverage: No tests exist.

**Slurm output format parsing:**
- Files: `slurm_monitor.py` (lines 519-540, 542-569)
- Why fragile: `_parse_squeue` and `_get_completed_job` parse pipe-delimited Slurm output by positional index. If Slurm's output format changes (column order, additional columns, locale-dependent formatting), parsing silently produces wrong results.
- Safe modification: Verify field count and consider using named format specifiers. Add assertions on expected field counts.
- Test coverage: No tests exist. Should have unit tests with sample Slurm output.

**Poll loop lifecycle management:**
- Files: `bot.py` (lines 428-480, 482-537)
- Why fragile: The poll loop is managed via a single `asyncio.Task` stored in a global variable. Race conditions are possible if `/watch` and `/stop` are called in rapid succession. Task cancellation is not awaited, so cleanup may not complete before a new task starts.
- Safe modification: Use a lock or event to coordinate watch/stop operations. Await task cancellation before allowing a new watch.
- Test coverage: No tests exist.

## Scaling Limits

**Single SSH connection:**
- Current capacity: One persistent SSH connection to one host.
- Limit: Cannot monitor multiple clusters. All commands are serialized through one connection.
- Scaling path: Support multiple `SSHManager` instances with a cluster registry. Would require refactoring the global state.

**In-memory state (tracked_jobs, pinned_jobs):**
- Current capacity: Works for a single user with dozens of jobs.
- Limit: All state is lost on bot restart. No persistence layer.
- Scaling path: Add SQLite or JSON file persistence for tracked/pinned jobs. Restore state on startup.

**Telegram message size limit:**
- Current capacity: Log output is truncated to ~3800 chars (`slurm_monitor.py` line 395). Other messages have no truncation.
- Limit: Telegram messages have a 4096-character limit. Commands like `/status` with many active jobs or `/history 50` could exceed this.
- Scaling path: Split long messages into multiple parts. The log command already truncates, but other commands do not.

## Dependencies at Risk

**python-telegram-bot pinned to >=21.0:**
- Risk: Very loose version constraint. Major version bumps (e.g., v22) could introduce breaking API changes.
- Impact: Bot startup or command handling could break after a `pip install --upgrade`.
- Migration plan: Pin to a specific minor version range (e.g., `>=21.0,<22.0`).

## Missing Critical Features

**No automatic SSH reconnection:**
- Problem: If the SSH connection drops (server reboot, network issue), the bot continues running but all commands fail until manually restarted.
- Blocks: Unattended long-running monitoring. Users must notice the failure and restart the bot.

**No state persistence:**
- Problem: Tracked jobs and pinned jobs are lost on bot restart. The poll loop does not resume.
- Blocks: Reliable monitoring across bot restarts or server reboots.

**No graceful shutdown of poll loop:**
- Problem: The signal handler calls `sys.exit(0)` which does not cleanly cancel the asyncio poll task. This could leave the SSH ControlMaster connection orphaned.
- Blocks: Clean restarts without stale SSH connections.

## Test Coverage Gaps

**Zero test coverage:**
- What's not tested: The entire codebase has no tests whatsoever. No unit tests, no integration tests, no end-to-end tests.
- Files: `bot.py`, `slurm_monitor.py`, `ssh_manager.py`
- Risk: Any refactoring or feature addition could break existing functionality without detection. The Slurm output parsing logic is particularly risky to change without tests, as edge cases in Slurm output formats are common.
- Priority: **High**. At minimum, add unit tests for:
  - `_parse_time_seconds()` and `_format_duration()` in `slurm_monitor.py`
  - `_parse_squeue()` with sample Slurm output
  - `_get_completed_job()` with sample sacct output
  - `is_authorized()` in `bot.py`
  - `SSHManager.run_command()` with mocked subprocess

---

*Concerns audit: 2026-04-08*
