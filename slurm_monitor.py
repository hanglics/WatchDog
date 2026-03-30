"""Slurm job status parser using squeue/sacct over SSH."""

import html
import re
from dataclasses import dataclass

from ssh_manager import SSHManager


def _esc(text: str) -> str:
    return html.escape(text)


def _parse_time_seconds(time_str: str) -> int:
    """Parse Slurm time format (D-HH:MM:SS or HH:MM:SS or MM:SS) to seconds."""
    time_str = time_str.strip()
    if not time_str or time_str == "UNLIMITED":
        return 0

    days = 0
    if "-" in time_str:
        day_part, time_str = time_str.split("-", 1)
        days = int(day_part)

    parts = time_str.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), int(parts[1])
    else:
        return 0

    return days * 86400 + h * 3600 + m * 60 + s


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    if seconds <= 0:
        return "N/A"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 24:
        d, h = divmod(h, 24)
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


@dataclass
class JobInfo:
    job_id: str
    name: str
    state: str
    time_used: str
    time_limit: str
    partition: str
    nodes: str
    node_list: str
    reason: str

    @property
    def is_running(self) -> bool:
        return self.state in ("RUNNING", "R")

    @property
    def is_pending(self) -> bool:
        return self.state in ("PENDING", "PD")

    @property
    def is_active(self) -> bool:
        return self.state in ("RUNNING", "R", "PENDING", "PD", "CONFIGURING", "CF")

    @property
    def state_emoji(self) -> str:
        if self.is_running:
            return "🟢"
        if self.is_pending:
            return "🟡"
        return "🔴"

    @property
    def label(self) -> str:
        return f"<code>{self.job_id}</code> (<b>{_esc(self.name)}</b>)"

    def format_short(self) -> str:
        return f"{self.state_emoji} {self.label} — {self.state} ({self.time_used})"

    def format_detail(self) -> str:
        lines = [
            f"{self.state_emoji} <b>Job {self.job_id}</b>: {_esc(self.name)}",
            f"  State: {self.state}",
            f"  Time Used: {self.time_used}",
            f"  Time Limit: {self.time_limit}",
            f"  Partition: {self.partition}",
            f"  Nodes: {self.nodes} ({_esc(self.node_list)})",
        ]
        if self.reason and self.reason != "None":
            lines.append(f"  Reason: {_esc(self.reason)}")
        return "\n".join(lines)


class SlurmMonitor:
    """Query Slurm job status over SSH."""

    def __init__(self, ssh: SSHManager, user: str | None = None):
        self.ssh = ssh
        self.slurm_user = user or ssh.user

    # ------------------------------------------------------------------
    # Core queries
    # ------------------------------------------------------------------

    def get_active_jobs(self) -> list[JobInfo]:
        """Get all active jobs (running + pending) for the user."""
        cmd = (
            f"squeue -u {self.slurm_user} "
            f"--format='%i|%j|%T|%M|%l|%P|%D|%R|%r' "
            f"--noheader"
        )
        stdout, stderr, rc = self.ssh.run_command(cmd)
        if rc != 0:
            raise RuntimeError(f"squeue failed: {stderr}")
        return self._parse_squeue(stdout)

    def get_job_detail(self, job_id: str) -> JobInfo | None:
        """Get details for a specific job (active or completed)."""
        cmd = (
            f"squeue -j {job_id} "
            f"--format='%i|%j|%T|%M|%l|%P|%D|%R|%r' "
            f"--noheader"
        )
        stdout, stderr, rc = self.ssh.run_command(cmd)
        if rc == 0 and stdout.strip():
            jobs = self._parse_squeue(stdout)
            if jobs:
                return jobs[0]
        return self._get_completed_job(job_id)

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel_job(self, job_id: str) -> str:
        """Cancel a single job. Returns status message."""
        _, stderr, rc = self.ssh.run_command(f"scancel {job_id}")
        if rc != 0:
            return f"❌ Failed to cancel job {job_id}: {_esc(stderr.strip())}"
        return f"🗑️ Job <code>{job_id}</code> cancelled."

    def cancel_all_jobs(self) -> str:
        """Cancel all jobs for this user."""
        _, stderr, rc = self.ssh.run_command(f"scancel -u {self.slurm_user}")
        if rc != 0:
            return f"❌ Failed to cancel jobs: {_esc(stderr.strip())}"
        return "🗑️ All jobs cancelled."

    # ------------------------------------------------------------------
    # History & failed
    # ------------------------------------------------------------------

    def get_history(self, count: int = 10) -> str:
        """Recent completed jobs via sacct."""
        cmd = (
            f"sacct -u {self.slurm_user} "
            f"--format='JobID,JobName%30,State,Elapsed,ExitCode,End' "
            f"--noheader --parsable2 "
            f"--starttime=now-7days "
            f"| grep -v '\\.' | tail -n {count}"
        )
        stdout, stderr, rc = self.ssh.run_command(cmd, timeout=30)
        if rc != 0 or not stdout.strip():
            return "No recent job history found."

        lines = ["📜 <b>Recent Jobs</b> (last {}):\n".format(count)]
        for row in stdout.strip().splitlines():
            parts = row.split("|")
            if len(parts) < 6:
                continue
            jid, name, state, elapsed, exit_code, end = (p.strip() for p in parts[:6])
            emoji = "✅" if "COMPLETED" in state else "❌" if "FAIL" in state or "CANCEL" in state else "⚪"
            lines.append(
                f"{emoji} <code>{jid}</code> <b>{_esc(name)}</b>\n"
                f"    {state} | {elapsed} | exit:{exit_code} | {end}"
            )
        return "\n".join(lines)

    def get_failed_jobs(self) -> str:
        """Recently failed/cancelled jobs."""
        cmd = (
            f"sacct -u {self.slurm_user} "
            f"--format='JobID,JobName%30,State,Elapsed,ExitCode,End' "
            f"--noheader --parsable2 "
            f"--starttime=now-7days --state=FAILED,CANCELLED,TIMEOUT,NODE_FAIL,OUT_OF_MEMORY "
            f"| grep -v '\\.'"
        )
        stdout, stderr, rc = self.ssh.run_command(cmd, timeout=30)
        if not stdout.strip():
            return "✅ No failed jobs in the last 7 days."

        lines = ["💥 <b>Failed Jobs</b> (last 7 days):\n"]
        for row in stdout.strip().splitlines():
            parts = row.split("|")
            if len(parts) < 6:
                continue
            jid, name, state, elapsed, exit_code, end = (p.strip() for p in parts[:6])
            lines.append(
                f"❌ <code>{jid}</code> <b>{_esc(name)}</b>\n"
                f"    {state} | {elapsed} | exit:{exit_code} | {end}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # ETA
    # ------------------------------------------------------------------

    def get_eta(self, job_id: str) -> str:
        """Estimated remaining time for a job."""
        job = self.get_job_detail(job_id)
        if not job:
            return f"Job {job_id} not found."

        if job.is_pending:
            # Try to get estimated start time
            cmd = f"squeue -j {job_id} --format='%S' --noheader"
            stdout, _, _ = self.ssh.run_command(cmd)
            start_est = stdout.strip().strip("'")
            if start_est and start_est != "N/A":
                return f"🟡 Job {job.label} is PENDING.\n  Estimated start: {start_est}"
            return f"🟡 Job {job.label} is PENDING. No estimated start time."

        if not job.is_running:
            return f"Job {job.label} is {job.state} (not running)."

        used_s = _parse_time_seconds(job.time_used)
        limit_s = _parse_time_seconds(job.time_limit)
        remaining_s = max(0, limit_s - used_s)

        return (
            f"⏱️ Job {job.label}\n"
            f"  Used: {job.time_used}\n"
            f"  Limit: {job.time_limit}\n"
            f"  Max remaining: <b>{_format_duration(remaining_s)}</b>"
        )

    # ------------------------------------------------------------------
    # Queue info
    # ------------------------------------------------------------------

    def get_queue_info(self) -> str:
        """Cluster-wide queue summary."""
        # Overall stats
        cmd_total = "squeue --noheader | wc -l"
        cmd_running = "squeue --noheader -t RUNNING | wc -l"
        cmd_pending = "squeue --noheader -t PENDING | wc -l"
        cmd_my_pos = (
            f"squeue -u {self.slurm_user} -t PENDING "
            f"--format='%i|%Q' --noheader --sort=-Q"
        )
        # GPU partition info
        cmd_gpu = (
            "sinfo -p gpu_cuda --noheader "
            "--format='%P|%a|%D|%T|%C'"
        )

        results = {}
        for label, cmd in [
            ("total", cmd_total),
            ("running", cmd_running),
            ("pending", cmd_pending),
            ("my_pending", cmd_my_pos),
            ("gpu", cmd_gpu),
        ]:
            stdout, _, _ = self.ssh.run_command(cmd, timeout=15)
            results[label] = stdout.strip()

        total = results["total"].strip()
        running = results["running"].strip()
        pending = results["pending"].strip()

        lines = [
            "📊 <b>Cluster Queue</b>\n",
            f"  Total jobs: {total}",
            f"  Running: {running}",
            f"  Pending: {pending}",
        ]

        # My pending jobs with priority
        if results["my_pending"]:
            lines.append("\n<b>Your pending jobs (by priority):</b>")
            for row in results["my_pending"].splitlines()[:10]:
                row = row.strip().strip("'")
                if "|" in row:
                    jid, prio = row.split("|", 1)
                    lines.append(f"  <code>{jid.strip()}</code> priority: {prio.strip()}")

        # GPU partition
        if results["gpu"]:
            lines.append("\n<b>GPU partition (gpu_cuda):</b>")
            for row in results["gpu"].splitlines()[:5]:
                row = row.strip().strip("'")
                lines.append(f"  {_esc(row)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Fairshare
    # ------------------------------------------------------------------

    def get_fairshare(self) -> str:
        """User's fairshare / priority info."""
        cmd = (
            f"sshare -u {self.slurm_user} --noheader "
            f"--format='Account,User,RawShares,NormShares,RawUsage,NormUsage,FairShare'"
        )
        stdout, stderr, rc = self.ssh.run_command(cmd, timeout=15)
        if rc != 0 or not stdout.strip():
            return f"Could not retrieve fairshare info: {_esc(stderr.strip())}"

        lines = ["⚖️ <b>Fairshare</b>\n"]
        lines.append(
            "<code>Account       User    RawShares NormShares RawUsage  NormUsage FairShare</code>"
        )
        for row in stdout.strip().splitlines():
            lines.append(f"<code>{_esc(row.rstrip())}</code>")

        # Also get priority of pending jobs
        cmd_prio = (
            f"sprio -u {self.slurm_user} --noheader "
            f"--format='%.10i %.10Y %.10A %.10F %.10J %.10P %.10Q' "
            f"| head -10"
        )
        pstdout, _, prc = self.ssh.run_command(cmd_prio, timeout=15)
        if prc == 0 and pstdout.strip():
            lines.append("\n<b>Pending job priorities:</b>")
            lines.append("<code>JobID      Priority   Age        FairShare  JobSize    Partition  QOS</code>")
            for row in pstdout.strip().splitlines():
                lines.append(f"<code>{_esc(row.rstrip())}</code>")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Logs & output
    # ------------------------------------------------------------------

    def get_job_log(self, job_id: str, grep_pattern: str | None = None, tail_lines: int = 30) -> str:
        """Tail the stdout file of a job, optionally grep for a pattern."""
        # Get the stdout path from scontrol
        cmd = f"scontrol show job {job_id} 2>/dev/null | grep StdOut"
        stdout, _, rc = self.ssh.run_command(cmd, timeout=15)

        if rc != 0 or not stdout.strip():
            # Try sacct for completed jobs
            cmd2 = f"sacct -j {job_id} --format='JobID,WorkDir' --noheader --parsable2 | grep -v '\\.' | head -1"
            stdout2, _, _ = self.ssh.run_command(cmd2, timeout=15)
            if stdout2.strip():
                parts = stdout2.strip().split("|")
                if len(parts) >= 2:
                    workdir = parts[1].strip()
                    # Try common patterns
                    cmd3 = f"ls -t {workdir}/slurm-{job_id}*.out 2>/dev/null | head -1"
                    stdout3, _, _ = self.ssh.run_command(cmd3, timeout=15)
                    if stdout3.strip():
                        log_path = stdout3.strip()
                    else:
                        return f"Could not find log file for job {job_id}."
                else:
                    return f"Could not find log file for job {job_id}."
            else:
                return f"Could not find log file for job {job_id}."
        else:
            match = re.search(r"StdOut=(.+)", stdout)
            if not match:
                return f"Could not parse log path for job {job_id}."
            log_path = match.group(1).strip()

        if grep_pattern:
            safe_pattern = grep_pattern.replace("'", "'\\''")
            cmd_read = f"grep -i '{safe_pattern}' {log_path} | tail -n {tail_lines}"
        else:
            cmd_read = f"tail -n {tail_lines} {log_path}"

        log_stdout, log_stderr, log_rc = self.ssh.run_command(cmd_read, timeout=30)

        if log_rc != 0 and not log_stdout.strip():
            return f"Could not read log: {_esc(log_stderr.strip())}"

        if not log_stdout.strip():
            if grep_pattern:
                return f"No matches for '{_esc(grep_pattern)}' in log of job {job_id}."
            return f"Log file is empty for job {job_id}."

        # Truncate to ~4000 chars for Telegram message limit
        text = log_stdout.strip()
        if len(text) > 3800:
            text = text[-3800:]
            text = "...(truncated)\n" + text

        header = f"📄 <b>Log for job {job_id}</b>"
        if grep_pattern:
            header += f" (grep: {_esc(grep_pattern)})"
        header += f"\n<code>{log_path}</code>\n"

        return f"{header}\n<pre>{_esc(text)}</pre>"

    def get_job_output_path(self, job_id: str) -> str:
        """Get the output file path for a job."""
        cmd = f"scontrol show job {job_id} 2>/dev/null | grep -E 'StdOut|StdErr|WorkDir'"
        stdout, _, rc = self.ssh.run_command(cmd, timeout=15)

        if rc != 0 or not stdout.strip():
            # Fallback to sacct
            cmd2 = f"sacct -j {job_id} --format='JobID,WorkDir' --noheader --parsable2 | grep -v '\\.' | head -1"
            stdout2, _, _ = self.ssh.run_command(cmd2, timeout=15)
            if stdout2.strip():
                parts = stdout2.strip().split("|")
                if len(parts) >= 2:
                    return f"📁 <b>Job {job_id}</b>\n  WorkDir: <code>{_esc(parts[1].strip())}</code>"
            return f"Could not find output info for job {job_id}."

        lines = [f"📁 <b>Job {job_id} output paths</b>\n"]
        for row in stdout.strip().splitlines():
            row = row.strip()
            if "=" in row:
                key, val = row.split("=", 1)
                lines.append(f"  {key.strip()}: <code>{_esc(val.strip())}</code>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def get_daily_summary(self) -> str:
        """Summary of today's job activity."""
        cmd = (
            f"sacct -u {self.slurm_user} "
            f"--format='JobID,JobName%30,State,Elapsed,ExitCode' "
            f"--noheader --parsable2 "
            f"--starttime=midnight "
            f"| grep -v '\\.'"
        )
        stdout, _, rc = self.ssh.run_command(cmd, timeout=30)

        # Current active
        active = self.get_active_jobs()
        running = [j for j in active if j.is_running]
        pending = [j for j in active if j.is_pending]

        completed = 0
        failed = 0
        cancelled = 0
        total_elapsed_s = 0

        if stdout.strip():
            for row in stdout.strip().splitlines():
                parts = row.split("|")
                if len(parts) < 5:
                    continue
                state = parts[2].strip()
                elapsed = parts[3].strip()
                if "COMPLETED" in state:
                    completed += 1
                elif "FAIL" in state or "TIMEOUT" in state or "OUT_OF_MEMORY" in state:
                    failed += 1
                elif "CANCEL" in state:
                    cancelled += 1
                total_elapsed_s += _parse_time_seconds(elapsed)

        lines = [
            "📋 <b>Daily Summary</b>\n",
            f"🟢 Running: {len(running)}",
            f"🟡 Pending: {len(pending)}",
            f"✅ Completed today: {completed}",
            f"❌ Failed today: {failed}",
            f"🚫 Cancelled today: {cancelled}",
            f"⏱️ Total compute time today: {_format_duration(total_elapsed_s)}",
        ]

        if running:
            lines.append("\n<b>Currently running:</b>")
            for j in running:
                lines.append(f"  {j.format_short()}")

        if pending:
            lines.append("\n<b>Queued:</b>")
            for j in pending[:10]:
                lines.append(f"  {j.format_short()}")
            if len(pending) > 10:
                lines.append(f"  ... and {len(pending) - 10} more")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def get_summary(self) -> str:
        """Get a formatted summary of all active jobs."""
        jobs = self.get_active_jobs()

        if not jobs:
            return "✅ No active jobs. All done!"

        running = [j for j in jobs if j.is_running]
        pending = [j for j in jobs if j.is_pending]

        lines = [f"📊 <b>Active Jobs: {len(jobs)}</b> (🟢 {len(running)} running, 🟡 {len(pending)} pending)"]
        lines.append("")

        for job in sorted(jobs, key=lambda j: (not j.is_running, j.job_id)):
            lines.append(job.format_short())

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse_squeue(self, stdout: str) -> list[JobInfo]:
        jobs = []
        for line in stdout.strip().splitlines():
            line = line.strip().strip("'")
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 9:
                continue
            jobs.append(JobInfo(
                job_id=parts[0].strip(),
                name=parts[1].strip(),
                state=parts[2].strip(),
                time_used=parts[3].strip(),
                time_limit=parts[4].strip(),
                partition=parts[5].strip(),
                nodes=parts[6].strip(),
                node_list=parts[7].strip(),
                reason=parts[8].strip(),
            ))
        return jobs

    def _get_completed_job(self, job_id: str) -> JobInfo | None:
        cmd = (
            f"sacct -j {job_id} "
            f"--format='JobID,JobName,State,Elapsed,Timelimit,Partition,NNodes,NodeList' "
            f"--noheader --parsable2"
        )
        stdout, _, rc = self.ssh.run_command(cmd)
        if rc != 0 or not stdout.strip():
            return None

        for line in stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) < 8:
                continue
            if "." in parts[0]:
                continue
            return JobInfo(
                job_id=parts[0].strip(),
                name=parts[1].strip(),
                state=parts[2].strip(),
                time_used=parts[3].strip(),
                time_limit=parts[4].strip(),
                partition=parts[5].strip(),
                nodes=parts[6].strip(),
                node_list=parts[7].strip(),
                reason="",
            )
        return None
