"""Platform process identity snapshots for safe orphan reconciliation."""

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """PID plus an OS creation fingerprint that detects PID reuse."""

    pid: int
    start_token: str
    process_group_id: int | None
    containment_ref: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (not isinstance(self.pid, int) or isinstance(self.pid, bool) or self.pid <= 0
                or not isinstance(self.start_token, str) or not self.start_token.strip()
                or (self.containment_ref is not None
                    and (not isinstance(self.containment_ref, str)
                         or not self.containment_ref.strip()
                         or "\x00" in self.containment_ref))
                or (self.process_group_id is not None
                    and (not isinstance(self.process_group_id, int)
                         or isinstance(self.process_group_id, bool)
                         or self.process_group_id <= 0))):
            raise ValueError("invalid process identity")


class ProcessObservation(str, Enum):
    """Read-only host observation of a persisted process identity."""

    MATCHES = "matches"
    ABSENT = "absent"
    PID_REUSED = "pid_reused"
    UNVERIFIABLE = "unverifiable"


def observe_process_identity(identity: ProcessIdentity) -> ProcessObservation:
    """Compare a persisted identity with the host without signaling any process.

    This is diagnostic only. In particular, ABSENT does not prove that child
    processes have exited, and UNVERIFIABLE never implies that it is safe to
    release invocation liveness.
    """
    if not isinstance(identity, ProcessIdentity):
        raise ValueError("identity must be a ProcessIdentity")
    try:
        current = capture_process_identity(identity.pid)
    except FileNotFoundError:
        return ProcessObservation.ABSENT
    except (OSError, ProcessLookupError, PermissionError, ValueError):
        return ProcessObservation.UNVERIFIABLE
    if (current.start_token == identity.start_token
            and current.process_group_id == identity.process_group_id):
        return ProcessObservation.MATCHES
    return ProcessObservation.PID_REUSED


def capture_process_identity(pid: int) -> ProcessIdentity:
    """Capture a PID's stable start fingerprint on supported host platforms.

    Linux combines the boot ID and `/proc/<pid>/stat` start ticks, preventing a
    reused PID across either process or host lifetime from matching. Windows
    uses the kernel process creation FILETIME. Unknown platforms fail closed.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    if os.name == "nt":
        return _capture_windows(pid)
    if os.name == "posix" and Path("/proc/sys/kernel/random/boot_id").is_file():
        return _capture_linux(pid)
    raise OSError("stable process identity is unsupported on this platform")


def process_identity_matches(identity: ProcessIdentity) -> bool:
    """Return whether the same OS process instance still owns this PID."""
    return observe_process_identity(identity) is ProcessObservation.MATCHES


def _capture_linux(pid: int) -> ProcessIdentity:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat.rfind(")")
    if close < 0:
        raise OSError("malformed Linux process stat record")
    fields = stat[close + 1:].split()
    # After the parenthesized command, index 0 is field 3 (state).
    if len(fields) <= 19:
        raise OSError("incomplete Linux process stat record")
    process_group_id = int(fields[2])
    start_ticks = int(fields[19])
    return ProcessIdentity(pid, f"linux:{boot_id}:{start_ticks}", process_group_id)


def _capture_windows(pid: int) -> ProcessIdentity:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        raise OSError(error, "OpenProcess failed while capturing process identity")

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
    try:
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            error = ctypes.get_last_error()
            raise OSError(error, "GetProcessTimes failed while capturing process identity")
        creation_time = (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)
    return ProcessIdentity(pid, f"windows:{creation_time}", None)
