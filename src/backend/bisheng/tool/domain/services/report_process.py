"""Terminate only the process tree owned by a report conversion."""

import asyncio
import os
import signal

from loguru import logger


class ProcessOwner:
    def __init__(self):
        self.handle = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            self.kernel = kernel
            self.handle = kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())

            class BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("process_time", ctypes.c_int64),
                    ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD),
                    ("min_ws", ctypes.c_size_t),
                    ("max_ws", ctypes.c_size_t),
                    ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD),
                ]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [
                    ("basic", BasicLimits),
                    ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t),
                ]

            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                kernel.CloseHandle(self.handle)
                raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, pid):
        if self.handle:
            import ctypes

            process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            try:
                if not process or not self.kernel.AssignProcessToJobObject(self.handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if process:
                    self.kernel.CloseHandle(process)

    async def close(self, process):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # The entire owned group may already have exited normally.
                pass
        if process is not None:
            await process.wait()


async def execute_report(directory, timeout=120):
    import sys

    owner = ProcessOwner()
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "bisheng.tool.domain.services.report_worker",
            str(directory),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        owner.attach(process.pid)
        _, error = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            logger.error("Report renderer failed: {}", error.decode("utf-8", errors="replace")[-2000:])
            raise RuntimeError("Report conversion failed; check the configured renderer and backend diagnostics")
    finally:
        await owner.close(process)
