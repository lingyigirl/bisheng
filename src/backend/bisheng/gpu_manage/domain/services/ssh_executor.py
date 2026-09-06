"""Synchronous paramiko SSH session wrapped for async handlers."""

import asyncio
import time
from dataclasses import dataclass

import paramiko
from loguru import logger

from bisheng.common.services.config_service import settings


@dataclass
class SSHResult:
    exit_code: int
    stdout: str
    stderr: str


class SSHCommandError(Exception):
    """Raised when a remote command exits non-zero."""

    def __init__(self, command: str, result: SSHResult):
        self.command = command
        self.result = result
        super().__init__(f"command failed (exit {result.exit_code}): {command[:200]}")


class SSHSession:
    """One short-lived SSH connection per operation."""

    def __init__(self, host: str, port: int, username: str, password: str, timeout: int | None = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout or settings.gpu_manage.ssh_timeout

    def run(self, command: str, timeout: int | None = None) -> SSHResult:
        command_timeout = timeout or settings.gpu_manage.command_timeout
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            _stdin, stdout, stderr = client.exec_command(command, timeout=command_timeout)
            # recv() blocks; enforce a deadline manually for robustness.
            deadline = time.monotonic() + command_timeout
            while not stdout.channel.exit_status_ready():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"SSH command timed out after {command_timeout}s: {command[:200]}")
                time.sleep(0.1)
            out_bytes = stdout.read()
            err_bytes = stderr.read()
            exit_code = stdout.channel.recv_exit_status()
            logger.debug("ssh {}@{}:{} -> {} (exit {})", self.username, self.host, self.port, command[:120], exit_code)
            return SSHResult(
                exit_code=exit_code,
                stdout=out_bytes.decode("utf-8", errors="replace"),
                stderr=err_bytes.decode("utf-8", errors="replace"),
            )
        finally:
            client.close()


async def run_ssh(session: SSHSession, command: str, timeout: int | None = None) -> SSHResult:
    return await asyncio.to_thread(session.run, command, timeout)
