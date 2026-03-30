"""SSH connection manager using ControlMaster for persistent MFA sessions."""

import subprocess
import os
import time
from pathlib import Path


class SSHManager:
    """Manages a persistent SSH connection via ControlMaster.

    After initial login+MFA, all subsequent commands reuse the
    authenticated connection without re-prompting.
    """

    def __init__(self, user: str, host: str, control_dir: str | None = None):
        self.user = user
        self.host = host
        self.control_dir = Path(control_dir or os.path.expanduser("~/.ssh/bunya-monitor"))
        self.control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.control_path = self.control_dir / "ctrl-%r@%h:%p"

    @property
    def is_connected(self) -> bool:
        result = subprocess.run(
            [
                "ssh", "-o", f"ControlPath={self.control_path}",
                "-O", "check",
                f"{self.user}@{self.host}",
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def connect(self) -> bool:
        """Open a persistent SSH connection. Requires interactive login+MFA."""
        if self.is_connected:
            print("[SSH] Already connected.")
            return True

        print(f"[SSH] Connecting to {self.user}@{self.host} ...")
        print("[SSH] You will be prompted for password and MFA.")
        print("[SSH] After authentication, the connection stays open in background.")

        result = subprocess.run(
            [
                "ssh",
                "-o", f"ControlPath={self.control_path}",
                "-o", "ControlMaster=yes",
                "-o", "ControlPersist=yes",
                "-o", "ServerAliveInterval=60",
                "-o", "ServerAliveCountMax=10",
                "-N", "-f",
                f"{self.user}@{self.host}",
            ],
        )

        if result.returncode != 0:
            print("[SSH] Connection failed.")
            return False

        # Wait briefly for control socket to be ready
        for _ in range(10):
            if self.is_connected:
                print("[SSH] Connected successfully.")
                return True
            time.sleep(0.5)

        print("[SSH] Connection may have failed (control socket not ready).")
        return False

    def run_command(self, command: str, timeout: int = 30) -> tuple[str, str, int]:
        """Run a command over the persistent SSH connection.

        Returns (stdout, stderr, returncode).
        """
        result = subprocess.run(
            [
                "ssh",
                "-o", f"ControlPath={self.control_path}",
                "-o", "ControlMaster=no",
                f"{self.user}@{self.host}",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout, result.stderr, result.returncode

    def disconnect(self) -> None:
        """Close the persistent SSH connection."""
        if not self.is_connected:
            return
        subprocess.run(
            [
                "ssh",
                "-o", f"ControlPath={self.control_path}",
                "-O", "exit",
                f"{self.user}@{self.host}",
            ],
            capture_output=True,
        )
        print("[SSH] Disconnected.")
