"""Telegram bot for Bunya Slurm job monitoring."""

import asyncio
import html
import logging
import os
import signal
import sys

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from ssh_manager import SSHManager
from slurm_monitor import SlurmMonitor

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Global state
ssh: SSHManager | None = None
monitor: SlurmMonitor | None = None
polling_task: asyncio.Task | None = None
# {job_id: job_name} for tracked jobs
tracked_jobs: dict[str, str] = {}
# {job_id: job_name} for pinned jobs (always shown in /status)
pinned_jobs: dict[str, str] = {}
MAX_TELEGRAM_MESSAGE = 3800
GROUP_BY_ALIASES = {
    "qos": "qos",
    "qoses": "qos",
    "partition": "partition",
    "partitions": "partition",
    "part": "partition",
}
WATCH_USAGE = (
    "Usage: /watch, /watch &lt;id1,id2,...&gt;, "
    "/watch qos &lt;name&gt;, /watch partition &lt;name&gt;, or /watch list"
)


def get_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        logger.error(f"Missing env var: {key}")
        sys.exit(1)
    return value


def _poll_interval() -> int:
    return int(os.getenv("POLL_INTERVAL", "60"))


def is_authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id)
    allowed = os.getenv("TELEGRAM_CHAT_ID", "")
    return chat_id == allowed


async def _reply(update: Update, text: str) -> None:
    """Send an HTML-formatted reply, falling back to plain text on parse error."""
    for chunk in _split_message(text):
        try:
            await update.message.reply_text(chunk, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(chunk)


async def _send(bot, chat_id: int, text: str) -> None:
    for chunk in _split_message(text):
        try:
            await bot.send_message(chat_id, chunk, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id, chunk)


def _check_ssh() -> str | None:
    """Returns an error message if SSH is down, else None."""
    if not ssh or not ssh.is_connected:
        return "❌ SSH not connected. Run the monitor script first."
    return None


def _split_message(text: str, limit: int = MAX_TELEGRAM_MESSAGE) -> list[str]:
    """Split long Telegram messages on line boundaries when possible."""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = []
    current_len = 0

    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append("".join(current).rstrip("\n"))
                current = []
                current_len = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start:start + limit].rstrip("\n"))
            continue

        if current and current_len + len(line) > limit:
            chunks.append("".join(current).rstrip("\n"))
            current = []
            current_len = 0

        current.append(line)
        current_len += len(line)

    if current:
        chunks.append("".join(current).rstrip("\n"))

    return chunks or [text]


def _parse_group_by(args: list[str], command: str) -> str:
    if not args:
        return "qos"
    if len(args) == 1 and args[0].lower() in GROUP_BY_ALIASES:
        return GROUP_BY_ALIASES[args[0].lower()]
    raise ValueError(f"Usage: /{command} [qos|partition]")


def _add_watch_filter(filters: dict[str, str], key: str, value: str) -> None:
    values = [part.strip() for part in value.split(",") if part.strip()]
    if not values:
        raise ValueError(WATCH_USAGE)
    existing = filters.get(key)
    filters[key] = ",".join([existing, *values] if existing else values)


def _parse_watch_args(args: list[str]) -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    filters: dict[str, str] = {}
    filter_keys = {
        "qos": "qos",
        "--qos": "qos",
        "partition": "partition",
        "--partition": "partition",
        "part": "partition",
        "--part": "partition",
    }
    filter_prefixes = {
        "qos=": "qos",
        "--qos=": "qos",
        "partition=": "partition",
        "--partition=": "partition",
        "part=": "partition",
        "--part=": "partition",
    }

    index = 0
    while index < len(args):
        token = args[index].strip()
        lower = token.lower()

        matched_filter = False
        for prefix, key in filter_prefixes.items():
            if lower.startswith(prefix):
                _add_watch_filter(filters, key, token[len(prefix):])
                matched_filter = True
                break
        if matched_filter:
            index += 1
            continue

        if lower in filter_keys:
            index += 1
            if index >= len(args):
                raise ValueError(WATCH_USAGE)
            _add_watch_filter(filters, filter_keys[lower], args[index])
            index += 1
            continue

        job_ids = [part.strip() for part in token.replace(",", " ").split() if part.strip()]
        if job_ids and all(job_id.isdigit() for job_id in job_ids):
            ids.extend(job_ids)
            index += 1
            continue

        raise ValueError(WATCH_USAGE)

    return ids, filters


def _matches_watch_filters(job, filters: dict[str, str]) -> bool:
    for attr, requested in filters.items():
        values = {part.strip().lower() for part in requested.split(",") if part.strip()}
        if values and getattr(job, attr, "").strip().lower() not in values:
            return False
    return True


def _format_watch_filters(filters: dict[str, str]) -> str:
    parts = []
    if filters.get("qos"):
        parts.append(f"QoS={html.escape(filters['qos'])}")
    if filters.get("partition"):
        parts.append(f"Partition={html.escape(filters['partition'])}")
    return f" matching {', '.join(parts)}" if parts else ""


# ======================================================================
# Commands
# ======================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("Unauthorized.")
        return
    await _reply(update,
        "🖥️ <b>Bunya Monitor Bot</b>\n\n"
        "<b>Status</b>\n"
        "/status [qos|partition] — All active jobs (+ pinned), grouped\n"
        "/status &lt;job_id&gt; — Specific job detail\n"
        "/eta &lt;job_id&gt; — Estimated remaining time\n"
        "/queue — Cluster queue overview\n"
        "/fairshare — Your fairshare / priority\n"
        "/summary [qos|partition] — Today's job activity digest\n"
        "/history [N] — Last N completed jobs\n"
        "/failed — Recently failed jobs\n\n"
        "<b>Logs</b>\n"
        "/log &lt;job_id&gt; — Tail last 30 lines of stdout\n"
        "/log &lt;job_id&gt; &lt;keyword&gt; — Grep log for keyword\n"
        "/output &lt;job_id&gt; — Show output file paths\n\n"
        "<b>Control</b>\n"
        "/cancel &lt;job_id&gt; — Cancel a job\n"
        "/cancel all — Cancel all your jobs\n"
        "/watch — Watch all active jobs\n"
        "/watch qos &lt;name&gt; — Watch active jobs in a QoS\n"
        "/watch partition &lt;name&gt; — Watch active jobs in a partition\n"
        "/watch &lt;id1,id2,...&gt; — Watch specific jobs\n"
        "/watch list — Show watched jobs\n"
        "/stop all — Stop watching all jobs\n"
        "/stop &lt;id1,id2,...&gt; — Stop watching specific jobs\n"
        "/pin &lt;job_id&gt; — Pin job to always show in /status\n"
        "/unpin &lt;job_id&gt; — Unpin a job\n"
        "/unpin all — Clear all pins\n"
        "/ssh — Check SSH connection\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    args = context.args
    try:
        if args and args[0].isdigit():
            job = monitor.get_job_detail(args[0])
            if job:
                await _reply(update, job.format_detail())
            else:
                await update.message.reply_text(f"Job {args[0]} not found.")
        else:
            group_by = _parse_group_by(args, "status")
            summary = monitor.get_summary(group_by=group_by)
            # Append pinned jobs that are no longer active
            if pinned_jobs:
                active_ids = {line.split("</code>")[0].split(">")[-1]
                              for line in summary.splitlines() if "<code>" in line}
                extra = []
                for jid, jname in pinned_jobs.items():
                    if jid not in active_ids:
                        info = monitor.get_job_detail(jid)
                        if info:
                            extra.append(f"📌 {info.format_short()}")
                        else:
                            extra.append(f"📌 <code>{jid}</code> (<b>{html.escape(jname)}</b>) — info unavailable")
                if extra:
                    summary += "\n\n<b>Pinned:</b>\n" + "\n".join(extra)
            await _reply(update, summary)
    except ValueError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /cancel &lt;job_id&gt; or /cancel all")
        return

    try:
        if args[0].lower() == "all":
            result = monitor.cancel_all_jobs()
        elif args[0].isdigit():
            result = monitor.cancel_job(args[0])
        else:
            await update.message.reply_text("Usage: /cancel &lt;job_id&gt; or /cancel all")
            return
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    count = 10
    if context.args and context.args[0].isdigit():
        count = min(int(context.args[0]), 50)

    try:
        result = monitor.get_history(count)
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_failed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    try:
        result = monitor.get_failed_jobs()
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_eta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /eta &lt;job_id&gt;")
        return

    try:
        result = monitor.get_eta(context.args[0])
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    try:
        result = monitor.get_queue_info()
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_fairshare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    try:
        result = monitor.get_fairshare()
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /log &lt;job_id&gt; [keyword]")
        return

    job_id = args[0]
    grep_pattern = " ".join(args[1:]) if len(args) > 1 else None

    try:
        result = monitor.get_job_log(job_id, grep_pattern=grep_pattern)
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_output(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /output &lt;job_id&gt;")
        return

    try:
        result = monitor.get_job_output_path(context.args[0])
        await _reply(update, result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    try:
        group_by = _parse_group_by(context.args, "summary")
        result = monitor.get_daily_summary(group_by=group_by)
        await _reply(update, result)
    except ValueError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    if not context.args or not context.args[0].isdigit():
        if pinned_jobs:
            lines = ["📌 <b>Pinned jobs:</b>\n"]
            for jid, jname in pinned_jobs.items():
                lines.append(f"  <code>{jid}</code> (<b>{html.escape(jname)}</b>)")
            await _reply(update, "\n".join(lines))
        else:
            await update.message.reply_text("No pinned jobs. Usage: /pin &lt;job_id&gt;")
        return

    job_id = context.args[0]
    try:
        info = monitor.get_job_detail(job_id)
        name = info.name if info else "unknown"
        pinned_jobs[job_id] = name
        await _reply(update, f"📌 Pinned job <code>{job_id}</code> (<b>{html.escape(name)}</b>)")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_unpin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text("Usage: /unpin &lt;job_id&gt; or /unpin all")
        return

    if context.args[0].lower() == "all":
        pinned_jobs.clear()
        await update.message.reply_text("📌 All pins cleared.")
    elif context.args[0] in pinned_jobs:
        name = pinned_jobs.pop(context.args[0])
        await _reply(update, f"📌 Unpinned <code>{context.args[0]}</code> (<b>{html.escape(name)}</b>)")
    else:
        await update.message.reply_text(f"Job {context.args[0]} is not pinned.")


# ======================================================================
# Watch / poll
# ======================================================================

def _format_watched_list() -> str:
    """Format the current watch list."""
    if not tracked_jobs:
        return "Not currently watching any jobs."
    lines = [f"👁️ <b>Watching {len(tracked_jobs)} jobs:</b>\n"]
    for jid, jname in tracked_jobs.items():
        lines.append(f"  <code>{jid}</code> (<b>{html.escape(jname)}</b>)")
    return "\n".join(lines)


async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start background polling.

    /watch           — watch all currently active jobs
    /watch id1,id2   — watch specific job IDs
    /watch qos gpu   — watch active jobs in a QoS
    /watch partition gpu_cuda — watch active jobs in a partition
    /watch list      — show currently watched jobs
    """
    if not is_authorized(update):
        return

    # /watch list — show current watch list
    if context.args and context.args[0].lower() == "list":
        await _reply(update, _format_watched_list())
        return

    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    global polling_task
    if polling_task and not polling_task.done():
        await update.message.reply_text("👁️ Already watching. Use /stop all to cancel first.")
        return

    interval = _poll_interval()
    tracked_jobs.clear()

    try:
        ids, filters = _parse_watch_args(context.args)
    except ValueError as e:
        await _reply(update, str(e))
        return

    if context.args:
        if ids:
            for jid in ids:
                info = monitor.get_job_detail(jid)
                if filters and (not info or not _matches_watch_filters(info, filters)):
                    continue
                tracked_jobs[jid] = info.name if info else "unknown"
        else:
            jobs = monitor.get_active_jobs(
                qos=filters.get("qos"),
                partition=filters.get("partition"),
            )
            for j in jobs:
                tracked_jobs[j.job_id] = j.name
    else:
        jobs = monitor.get_active_jobs()
        for j in jobs:
            tracked_jobs[j.job_id] = j.name

    if not tracked_jobs:
        await _reply(update, f"✅ No active jobs{_format_watch_filters(filters)} to watch.")
        return

    job_list = "\n".join(
        f"  <code>{jid}</code> (<b>{html.escape(jname)}</b>)"
        for jid, jname in tracked_jobs.items()
    )
    await _reply(update,
        f"👁️ Watching <b>{len(tracked_jobs)}</b> jobs{_format_watch_filters(filters)} "
        f"(poll every {interval}s):\n{job_list}"
    )

    polling_task = asyncio.create_task(
        _poll_loop(context.bot, update.effective_chat.id, interval)
    )


async def _poll_loop(bot, chat_id: int, interval: int) -> None:
    """Background loop: detect state changes and completion for tracked jobs."""
    prev_states: dict[str, str] = {}

    while True:
        await asyncio.sleep(interval)

        try:
            if not ssh.is_connected:
                await _send(bot, chat_id, "⚠️ SSH connection lost. Stopping watch.")
                break

            jobs = monitor.get_active_jobs()
            active_ids = {j.job_id for j in jobs}
            active_map = {j.job_id: j for j in jobs}
            current_states = {j.job_id: j.state for j in jobs}

            # Detect state changes
            for jid in list(tracked_jobs):
                if jid in active_map:
                    job = active_map[jid]
                    # Update name if we had "unknown"
                    if tracked_jobs[jid] == "unknown":
                        tracked_jobs[jid] = job.name
                    old_state = prev_states.get(jid)
                    if old_state and old_state != job.state:
                        name = html.escape(tracked_jobs[jid])
                        await _send(bot, chat_id,
                            f"🔄 <code>{jid}</code> (<b>{name}</b>): {old_state} → {job.state}"
                        )

            # Detect finished jobs
            for jid in list(tracked_jobs):
                if jid not in active_ids:
                    info = monitor.get_job_detail(jid)
                    state = info.state if info else "COMPLETED/UNKNOWN"
                    name = html.escape(tracked_jobs[jid])
                    emoji = "✅" if "COMPLETED" in state else "❌"
                    await _send(bot, chat_id,
                        f"{emoji} <code>{jid}</code> (<b>{name}</b>) finished: {state}"
                    )
                    del tracked_jobs[jid]

            prev_states = current_states

            if not tracked_jobs:
                await _send(bot, chat_id, "🎉 <b>All tracked jobs have finished!</b>")
                break

        except Exception as e:
            logger.error(f"Poll error: {e}")
            await _send(bot, chat_id, f"⚠️ Poll error: {html.escape(str(e))}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stop all — stop watching all jobs.
    /stop id1,id2 — stop watching specific jobs only.
    """
    if not is_authorized(update):
        return

    global polling_task
    if not polling_task or polling_task.done():
        await update.message.reply_text("Not currently watching.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /stop all or /stop &lt;id1,id2,...&gt;"
        )
        return

    if args[0].lower() == "all":
        polling_task.cancel()
        polling_task = None
        tracked_jobs.clear()
        await update.message.reply_text("🛑 Stopped watching all jobs.")
        return

    # Stop specific jobs
    raw = " ".join(args)
    ids_to_remove = [x.strip() for x in raw.replace(",", " ").split() if x.strip().isdigit()]
    if not ids_to_remove:
        await update.message.reply_text("Usage: /stop all or /stop &lt;id1,id2,...&gt;")
        return

    removed = []
    not_found = []
    for jid in ids_to_remove:
        if jid in tracked_jobs:
            name = tracked_jobs.pop(jid)
            removed.append(f"<code>{jid}</code> (<b>{html.escape(name)}</b>)")
        else:
            not_found.append(jid)

    lines = []
    if removed:
        lines.append("🛑 Stopped watching:\n" + "\n".join(f"  {r}" for r in removed))
    if not_found:
        lines.append(f"Not watched: {', '.join(not_found)}")
    if tracked_jobs:
        lines.append(f"\n👁️ Still watching {len(tracked_jobs)} jobs.")
    else:
        # No more jobs to watch — cancel the poll loop
        polling_task.cancel()
        polling_task = None
        lines.append("\n🛑 No jobs left. Stopped watching.")

    await _reply(update, "\n".join(lines))


async def cmd_ssh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    connected = ssh.is_connected if ssh else False
    status = "🟢 Connected" if connected else "🔴 Disconnected"
    await update.message.reply_text(f"SSH: {status}")


# ======================================================================
# Main
# ======================================================================

def main() -> None:
    global ssh, monitor

    token = get_env("TELEGRAM_BOT_TOKEN")
    user = get_env("BUNYA_USER")
    host = os.getenv("BUNYA_HOST", "bunya.rcc.uq.edu.au")

    ssh = SSHManager(user, host)
    if not ssh.connect():
        logger.error("Failed to establish SSH connection. Exiting.")
        sys.exit(1)

    monitor = SlurmMonitor(ssh, user)

    try:
        jobs = monitor.get_active_jobs()
        logger.info(f"Connected. Found {len(jobs)} active jobs.")
    except Exception as e:
        logger.error(f"SSH connected but squeue failed: {e}")
        sys.exit(1)

    app = Application.builder().token(token).build()

    handlers = {
        "start": cmd_start,
        "help": cmd_start,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "history": cmd_history,
        "failed": cmd_failed,
        "eta": cmd_eta,
        "queue": cmd_queue,
        "fairshare": cmd_fairshare,
        "log": cmd_log,
        "output": cmd_output,
        "summary": cmd_summary,
        "pin": cmd_pin,
        "unpin": cmd_unpin,
        "watch": cmd_watch,
        "stop": cmd_stop,
        "ssh": cmd_ssh,
    }
    for name, handler in handlers.items():
        app.add_handler(CommandHandler(name, handler))

    # Register command menu with Telegram so it shows in the "/" autocomplete
    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands([
            ("status", "All active jobs grouped by qos/partition"),
            ("eta", "Estimated remaining time for a job"),
            ("queue", "Cluster queue overview"),
            ("fairshare", "Your fairshare / priority"),
            ("summary", "Today's digest grouped by qos/partition"),
            ("history", "Last N completed jobs"),
            ("failed", "Recently failed jobs"),
            ("log", "Tail job stdout or grep keyword"),
            ("output", "Show job output file paths"),
            ("cancel", "Cancel a job or all jobs"),
            ("watch", "Watch jobs, optionally by qos/partition"),
            ("stop", "Stop watching all or specific jobs"),
            ("pin", "Pin a job to always show in /status"),
            ("unpin", "Unpin a job"),
            ("ssh", "Check SSH connection status"),
            ("help", "Show all commands"),
        ])
        logger.info("Registered command menu with Telegram.")

    app.post_init = post_init

    def shutdown(signum, frame):
        logger.info("Shutting down...")
        if ssh:
            ssh.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Bot is running. Send /start in Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
