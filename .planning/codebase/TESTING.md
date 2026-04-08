# Testing Patterns

**Analysis Date:** 2026-04-08

## Test Framework

**Runner:** None configured

**Assertion Library:** None configured

**Run Commands:**
```bash
# No test commands available -- no test infrastructure exists
```

## Current State

**No tests exist in this codebase.** There are:
- Zero test files (`*.test.*`, `*.spec.*`, `test_*.py`, `*_test.py`)
- No test framework in `requirements.txt` (no pytest, unittest references)
- No test configuration (`pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`)
- No CI/CD pipeline configuration detected

## Test File Organization

**Recommended location:** Co-located `tests/` directory at project root

**Recommended naming:** `test_<module>.py` (pytest convention)

**Recommended structure:**
```
bunya-monitor/
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Shared fixtures
│   ├── test_slurm_monitor.py
│   ├── test_ssh_manager.py
│   └── test_bot.py
```

## Recommended Test Structure

**Suite Organization:**
```python
# tests/test_slurm_monitor.py
import pytest
from slurm_monitor import SlurmMonitor, JobInfo, _parse_time_seconds, _format_duration


class TestParseTimeSeconds:
    def test_hhmmss(self):
        assert _parse_time_seconds("01:30:00") == 5400

    def test_days_hhmmss(self):
        assert _parse_time_seconds("1-12:00:00") == 129600

    def test_mmss(self):
        assert _parse_time_seconds("30:00") == 1800

    def test_unlimited(self):
        assert _parse_time_seconds("UNLIMITED") == 0

    def test_empty(self):
        assert _parse_time_seconds("") == 0


class TestFormatDuration:
    def test_minutes_seconds(self):
        assert _format_duration(125) == "2m 5s"

    def test_hours(self):
        assert _format_duration(3665) == "1h 1m 5s"

    def test_days(self):
        assert _format_duration(90000) == "1d 1h 0m"

    def test_zero(self):
        assert _format_duration(0) == "N/A"
```

## Mocking

**Recommended framework:** `unittest.mock` (stdlib) or `pytest-mock`

**Key mocking targets:**

1. **SSHManager.run_command()** -- mock SSH command execution for all SlurmMonitor tests:
```python
@pytest.fixture
def mock_ssh():
    ssh = MagicMock(spec=SSHManager)
    ssh.user = "testuser"
    ssh.is_connected = True
    return ssh

@pytest.fixture
def monitor(mock_ssh):
    return SlurmMonitor(mock_ssh, "testuser")

def test_get_active_jobs(monitor, mock_ssh):
    mock_ssh.run_command.return_value = (
        "12345|my_job|RUNNING|01:30:00|24:00:00|gpu_cuda|1|bun001|None\n",
        "",
        0,
    )
    jobs = monitor.get_active_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == "12345"
    assert jobs[0].is_running is True
```

2. **subprocess.run()** -- mock for SSHManager tests:
```python
@patch("ssh_manager.subprocess.run")
def test_is_connected(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    mgr = SSHManager("user", "host")
    assert mgr.is_connected is True
```

3. **Telegram Update/Context** -- mock for bot command tests:
```python
@pytest.fixture
def mock_update():
    update = MagicMock(spec=Update)
    update.effective_chat.id = 12345
    update.message.reply_text = AsyncMock()
    return update
```

**What to mock:**
- SSH connections and command execution
- Telegram API calls (send_message, reply_text)
- Environment variables (`monkeypatch.setenv`)
- `subprocess.run` calls

**What NOT to mock:**
- `_parse_squeue()` parsing logic
- `_parse_time_seconds()` / `_format_duration()` pure functions
- `JobInfo` dataclass and its properties
- `_esc()` HTML escaping

## Fixtures and Factories

**Recommended test data patterns:**
```python
# tests/conftest.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from slurm_monitor import SlurmMonitor, JobInfo
from ssh_manager import SSHManager


SAMPLE_SQUEUE_OUTPUT = (
    "12345|train_model|RUNNING|01:30:00|24:00:00|gpu_cuda|1|bun001|None\n"
    "12346|preprocess|PENDING|0:00|12:00:00|general|1|(Priority)|Priority\n"
)

SAMPLE_SACCT_OUTPUT = (
    "12340|old_job|COMPLETED|02:15:30|0:0|2026-04-08T10:00:00\n"
    "12341|bad_job|FAILED|00:05:12|1:0|2026-04-08T08:30:00\n"
)


def make_job(**overrides) -> JobInfo:
    """Factory for JobInfo test instances."""
    defaults = {
        "job_id": "99999",
        "name": "test_job",
        "state": "RUNNING",
        "time_used": "01:00:00",
        "time_limit": "24:00:00",
        "partition": "gpu_cuda",
        "nodes": "1",
        "node_list": "bun001",
        "reason": "None",
    }
    defaults.update(overrides)
    return JobInfo(**defaults)


@pytest.fixture
def mock_ssh():
    ssh = MagicMock(spec=SSHManager)
    ssh.user = "testuser"
    ssh.is_connected = True
    return ssh


@pytest.fixture
def monitor(mock_ssh):
    return SlurmMonitor(mock_ssh, "testuser")
```

**Location:** `tests/conftest.py` for shared fixtures

## Coverage

**Requirements:** None enforced (no coverage tooling configured)

**Recommended setup:**
```bash
pip install pytest pytest-cov pytest-asyncio
pytest --cov=. --cov-report=term-missing
```

**Add to `pyproject.toml`:**
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.coverage.run]
source = ["."]
omit = ["tests/*", ".venv/*"]

[tool.coverage.report]
fail_under = 80
```

## Test Types

**Unit Tests (highest priority):**
- Pure functions in `slurm_monitor.py`: `_parse_time_seconds()`, `_format_duration()`, `_parse_squeue()`
- `JobInfo` dataclass properties: `is_running`, `is_pending`, `is_active`, `state_emoji`, `format_short()`, `format_detail()`
- `SlurmMonitor` methods with mocked SSH: `get_active_jobs()`, `get_job_detail()`, `cancel_job()`, `get_eta()`, `get_history()`
- `SSHManager` with mocked `subprocess`: `is_connected`, `connect()`, `run_command()`

**Integration Tests (medium priority):**
- Bot command handlers with mocked SSH + Telegram: verify correct response formatting
- Authorization checks: verify unauthorized users are rejected
- SSH error handling: verify graceful degradation on connection loss

**E2E Tests:**
- Not applicable (requires live SSH connection to HPC cluster)
- Manual testing against real Bunya cluster is the current approach

## Testability Concerns

**Hard-to-test areas:**

1. **Global mutable state in `bot.py`**: `ssh`, `monitor`, `polling_task`, `tracked_jobs`, `pinned_jobs` are module globals. Testing requires careful setup/teardown or refactoring to dependency injection.

2. **`_poll_loop()` async background task**: Long-running loop with `asyncio.sleep()`. Requires `pytest-asyncio` and time mocking.

3. **`SSHManager.connect()`**: Opens real SSH connection with interactive MFA. Cannot be unit tested -- must be mocked.

4. **`main()` function**: Tightly coupled initialization. Consider extracting app factory pattern for testability.

5. **HTML output formatting**: Mixed into business logic methods. Hard to test output correctness without parsing HTML.

## Priority Test Targets

Files ranked by testability and value:

1. **`slurm_monitor.py`** -- Most testable. Pure parsing logic and data formatting. Mock SSH for integration-level methods.
2. **`ssh_manager.py`** -- Moderately testable. Mock `subprocess.run()` for all methods.
3. **`bot.py`** -- Least testable due to global state and Telegram API coupling. Refactor recommended before comprehensive testing.

---

*Testing analysis: 2026-04-08*
