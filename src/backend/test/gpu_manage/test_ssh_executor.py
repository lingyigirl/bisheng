"""Unit tests for the SSH executor (paramiko mocked at the module boundary)."""

from bisheng.gpu_manage.domain.services import ssh_executor
from bisheng.gpu_manage.domain.services.ssh_executor import SSHResult, SSHSession


class _FakeChannel:
    def __init__(self, out: bytes, err: bytes, exit_code: int):
        self._out = out
        self._err = err
        self._exit_code = exit_code

    def exit_status_ready(self) -> bool:
        return True

    def recv_exit_status(self) -> int:
        return self._exit_code


class _FakeStdout:
    def __init__(self, channel: _FakeChannel):
        self.channel = channel

    def read(self) -> bytes:
        return self.channel._out


class _FakeStderr:
    def __init__(self, channel: _FakeChannel):
        self.channel = channel

    def read(self) -> bytes:
        return self.channel._err


class _FakeClient:
    instances = []

    def __init__(self):
        self.closed = False
        self.commands = []
        _FakeClient.instances.append(self)

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, *args, **kwargs):
        pass

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        channel = _FakeChannel(b"hello\n", b"", 0)
        return object(), _FakeStdout(channel), _FakeStderr(channel)

    def close(self):
        self.closed = True


def test_run_returns_stdout_and_closes(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(ssh_executor.paramiko, "SSHClient", _FakeClient)

    session = SSHSession("10.0.0.1", 22, "root", "secret")
    result = session.run("nvidia-smi -L")

    assert result == SSHResult(exit_code=0, stdout="hello\n", stderr="")
    assert _FakeClient.instances[0].commands == ["nvidia-smi -L"]
    assert _FakeClient.instances[0].closed is True


def test_run_closes_on_failure(monkeypatch):
    _FakeClient.instances = []

    class _BoomClient(_FakeClient):
        def connect(self, *args, **kwargs):
            raise ConnectionError("refused")

    monkeypatch.setattr(ssh_executor.paramiko, "SSHClient", _BoomClient)

    session = SSHSession("10.0.0.1", 22, "root", "secret")
    try:
        session.run("whoami")
    except ConnectionError:
        pass
    assert _FakeClient.instances[0].closed is True
