"""Windows Job Object containment for invocation process trees."""

import ctypes
from ctypes import wintypes
import secrets
import subprocess
import time


_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_SYNCHRONIZE = 0x00100000
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ALREADY_EXISTS = 183


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


class WindowsJob:
    """A named kill-on-close job that can be safely reacquired by a successor."""

    def __init__(self, name: str, handle):
        self.name = name
        self._handle = handle

    @classmethod
    def create(cls) -> "WindowsJob":
        kernel32 = _kernel32()
        name = "Global\\CODEXDEVTEAM-" + secrets.token_hex(16)
        ctypes.set_last_error(0)
        handle = kernel32.CreateJobObjectW(None, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise FileExistsError("unique invocation Job Object name already exists")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        return cls(name, handle)

    def assign(self, process: subprocess.Popen) -> None:
        kernel32 = _kernel32()
        if not kernel32.AssignProcessToJobObject(
                self._handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process: subprocess.Popen) -> None:
        _resume_suspended_primary_thread(process.pid)

    def active_process_count(self) -> int:
        kernel32 = _kernel32()
        info = _BasicAccountingInformation()
        if not kernel32.QueryInformationJobObject(
                self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(info.ActiveProcesses)

    def terminate_and_verify(self, *, timeout_seconds: float = 5.0) -> bool:
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("Job Object termination timeout is outside its bounded range")
        try:
            if self.active_process_count() == 0:
                return True
            kernel32 = _kernel32()
            if not kernel32.TerminateJobObject(self._handle, 1):
                return self.active_process_count() == 0
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if self.active_process_count() == 0:
                    return True
                time.sleep(0.02)
            return self.active_process_count() == 0
        except (OSError, ValueError):
            return False

    def close(self) -> bool:
        handle, self._handle = self._handle, None
        if handle:
            return bool(_kernel32().CloseHandle(handle))
        return True


def reap_named_windows_job(name: str, *, pid: int, timeout_seconds: float = 5.0):
    """Reacquire and terminate one persisted invocation job, fail-closed."""
    from .process_reaper import ProcessReapResult, ProcessReapStatus

    if (not isinstance(name, str)
            or not __import__("re").fullmatch(r"Global\\CODEXDEVTEAM-[0-9a-f]{32}", name)
            or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0):
        raise ValueError("persisted Windows Job Object identity is malformed")
    kernel32 = _kernel32()
    handle = kernel32.OpenJobObjectW(
        _JOB_OBJECT_QUERY | _JOB_OBJECT_TERMINATE | _SYNCHRONIZE, False, name)
    if not handle:
        error = ctypes.get_last_error()
        if error in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
            # The persisted identity is written only after assignment and before
            # resume. A missing kill-on-close job therefore proves it is empty.
            return ProcessReapResult(ProcessReapStatus.TERMINATED,
                                     "windows_job_object_absent", pid, None,
                                     whole_tree_verified=True)
        return ProcessReapResult(ProcessReapStatus.UNVERIFIED,
                                 "windows_job_object", pid, None)
    job = WindowsJob(name, handle)
    try:
        verified = job.terminate_and_verify(timeout_seconds=timeout_seconds)
        return ProcessReapResult(
            ProcessReapStatus.TERMINATED if verified else ProcessReapStatus.UNVERIFIED,
            "windows_job_object", pid, None, whole_tree_verified=verified,
        )
    finally:
        job.close()


def _resume_suspended_primary_thread(pid: int) -> None:
    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32))
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32))
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.GetProcessIdOfThread.argtypes = (wintypes.HANDLE,)
    kernel32.GetProcessIdOfThread.restype = wintypes.DWORD
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    thread_handle = None
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                thread_handle = kernel32.OpenThread(0x0002 | 0x0800, False,
                                                    entry.th32ThreadID)
                if not thread_handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                if kernel32.GetProcessIdOfThread(thread_handle) != pid:
                    raise OSError("primary thread identity changed before resume")
                previous_suspend_count = kernel32.ResumeThread(thread_handle)
                if previous_suspend_count == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if previous_suspend_count != 1:
                    raise OSError("suspended process had an unexpected thread suspend count")
                return
            found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        raise ProcessLookupError("suspended process primary thread was not found")
    finally:
        if thread_handle:
            kernel32.CloseHandle(thread_handle)
        kernel32.CloseHandle(snapshot)


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.OpenJobObjectW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
