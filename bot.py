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
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(text)


async def _send(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        await bot.send_message(chat_id, text)


def _check_ssh() -> str | None:
    """Returns an error message if SSH is down, else None."""
    if not ssh or not ssh.is_connected:
        return "❌ SSH not connected. Run the monitor script first."
    return None


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
        "/status — All active jobs (+ pinned)\n"
        "/status &lt;job_id&gt; — Specific job detail\n"
        "/eta &lt;job_id&gt; — Estimated remaining time\n"
        "/queue — Cluster queue overview\n"
        "/fairshare — Your fairshare / priority\n"
        "/summary — Today's job activity digest\n"
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
        "/watch &lt;id1,id2,...&gt; — Watch specific jobs\n"
        "/stop — Stop watching\n"
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
            summary = monitor.get_summary()
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
        result = monitor.get_daily_summary()
        await _reply(update, result)
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

async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start background polling.

    /watch           — watch all currently active jobs
    /watch id1,id2   — watch specific job IDs
    """
    if not is_authorized(update):
        return
    if err := _check_ssh():
        await update.message.reply_text(err)
        return

    global polling_task
    if polling_task and not polling_task.done():
        await update.message.reply_text("👁️ Already watching. Use /stop to cancel first.")
        return

    interval = _poll_interval()
    tracked_jobs.clear()

    if context.args:
        # Parse comma-separated job IDs (also handles space-separated)
        raw = " ".join(context.args)
        ids = [x.strip() for x in raw.replace(",", " ").split() if x.strip().isdigit()]
        if not ids:
            await update.message.reply_text("Usage: /watch or /watch &lt;id1,id2,...&gt;")
            return
        for jid in ids:
            info = monitor.get_job_detail(jid)
            tracked_jobs[jid] = info.name if info else "unknown"
    else:
        jobs = monitor.get_active_jobs()
        for j in jobs:
            tracked_jobs[j.job_id] = j.name

    if not tracked_jobs:
        await update.message.reply_text("✅ No active jobs to watch.")
        return

    job_list = "\n".join(
        f"  <code>{jid}</code> (<b>{html.escape(jname)}</b>)"
        for jid, jname in tracked_jobs.items()
    )
    await _reply(update,
        f"👁️ Watching <b>{len(tracked_jobs)}</b> jobs (poll every {interval}s):\n{job_list}"
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
    if not is_authorized(update):
        return

    global polling_task
    if polling_task and not polling_task.done():
        polling_task.cancel()
        polling_task = None
        tracked_jobs.clear()
        await update.message.reply_text("🛑 Stopped watching.")
    else:
        await update.message.reply_text("Not currently watching.")


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
            ("status", "All active jobs (+ pinned)"),
            ("eta", "Estimated remaining time for a job"),
            ("queue", "Cluster queue overview"),
            ("fairshare", "Your fairshare / priority"),
            ("summary", "Today's job activity digest"),
            ("history", "Last N completed jobs"),
            ("failed", "Recently failed jobs"),
            ("log", "Tail job stdout or grep keyword"),
            ("output", "Show job output file paths"),
            ("cancel", "Cancel a job or all jobs"),
            ("watch", "Watch jobs, notify on changes"),
            ("stop", "Stop watching"),
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
