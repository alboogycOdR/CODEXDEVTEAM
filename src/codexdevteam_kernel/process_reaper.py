"""Fail-closed host process-group termination for stale maker invocations."""

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import select
import signal
import time

from .process_identity import ProcessIdentity, capture_process_identity


class ProcessReapStatus(str, Enum):
    TERMINATED = "terminated"
    GROUP_TERMINATED = "group_terminated"
    LEADER_ABSENT = "leader_absent_unverified"
    IDENTITY_MISMATCH = "identity_mismatch"
    GROUP_MISMATCH = "group_mismatch"
    SELF_GROUP_REFUSED = "self_group_refused"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "termination_unverified"


@dataclass(frozen=True, slots=True)
class ProcessReapResult:
    status: ProcessReapStatus
    method: str
    pid: int
    process_group_id: int | None
    whole_tree_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, ProcessReapStatus) or not isinstance(self.method, str):
            raise ValueError("process reaper result is malformed")
        if not isinstance(self.whole_tree_verified, bool):
            raise ValueError("whole_tree_verified must be boolean")
        if self.whole_tree_verified and self.status is not ProcessReapStatus.TERMINATED:
            raise ValueError("whole-tree verification requires full containment termination")

    @property
    def verified(self) -> bool:
        """Whether this backend verified its declared process-group scope."""
        return self.status in {ProcessReapStatus.TERMINATED,
                               ProcessReapStatus.GROUP_TERMINATED}


def reap_process_group(identity: ProcessIdentity, *, grace_seconds: float = 0.25,
                       timeout_seconds: float = 5.0) -> ProcessReapResult:
    """Terminate a Linux invocation group without a numeric-PGID reuse race.

    A pidfd pins the original identity. The group leader is stopped through
    that pidfd before group signals are sent, so its PID/PGID cannot be
    recycled while the numeric process-group operation is in flight. Success
    requires observing leader exit and no live (non-zombie) `/proc` member.
    Descendants that deliberately escaped the process group are outside this
    backend's containment boundary. Unsupported or uncertain cases fail
    closed; callers must keep invocation liveness held.
    """
    if not isinstance(identity, ProcessIdentity):
        raise ValueError("identity must be a ProcessIdentity")
    if (isinstance(grace_seconds, bool) or not isinstance(grace_seconds, (int, float))
            or not 0 <= grace_seconds <= 5
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.1 <= timeout_seconds <= 30):
        raise ValueError("reaper grace/timeout is outside its bounded range")
    if identity.process_group_id != identity.pid:
        return _result(ProcessReapStatus.GROUP_MISMATCH, identity)
    if identity.pid == os.getpid() or identity.process_group_id == os.getpgrp():
        return _result(ProcessReapStatus.SELF_GROUP_REFUSED, identity)
    if not _linux_pidfd_supported():
        return _result(ProcessReapStatus.UNSUPPORTED, identity)
    try:
        pidfd = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return _result(ProcessReapStatus.LEADER_ABSENT, identity)
    except OSError:
        return _result(ProcessReapStatus.UNVERIFIED, identity)

    stop_requested = False
    leader_exited = False
    try:
        try:
            observed = capture_process_identity(identity.pid)
        except (OSError, ValueError):
            return _result(ProcessReapStatus.UNVERIFIED, identity)
        if observed != identity:
            return _result(ProcessReapStatus.IDENTITY_MISMATCH, identity)

        signal.pidfd_send_signal(pidfd, signal.SIGSTOP)
        stop_requested = True
        deadline = time.monotonic() + min(float(timeout_seconds), 2.0)
        while time.monotonic() < deadline:
            state, current = _linux_process_state(identity.pid)
            if current != identity:
                return _result(ProcessReapStatus.IDENTITY_MISMATCH, identity)
            if state in {"T", "t"}:
                break
            if state in {"Z", "X"}:
                return _result(ProcessReapStatus.LEADER_ABSENT, identity)
            time.sleep(0.01)
        else:
            return _result(ProcessReapStatus.UNVERIFIED, identity)

        # The stopped leader and open pidfd keep this PGID anchored through
        # both group signals, preventing reuse of its numeric group ID.
        os.killpg(identity.process_group_id, signal.SIGTERM)
        time.sleep(float(grace_seconds))
        os.killpg(identity.process_group_id, signal.SIGKILL)

        deadline = time.monotonic() + float(timeout_seconds)
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        while time.monotonic() < deadline:
            leader_exited = bool(poller.poll(0))
            active = _linux_group_has_live_member(identity.process_group_id)
            if leader_exited and active is False:
                return _result(ProcessReapStatus.GROUP_TERMINATED, identity)
            if active is None:
                break
            time.sleep(0.02)
        return _result(ProcessReapStatus.UNVERIFIED, identity)
    except (OSError, ProcessLookupError, PermissionError, ValueError):
        return _result(ProcessReapStatus.UNVERIFIED, identity)
    finally:
        if stop_requested and not leader_exited:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGCONT)
            except OSError:
                pass
        os.close(pidfd)


def reap_managed_process(identity: ProcessIdentity, *, grace_seconds: float = 0.25,
                         timeout_seconds: float = 5.0) -> ProcessReapResult:
    """Select the host containment backend; never infer whole-tree proof on Linux."""
    if not isinstance(identity, ProcessIdentity):
        raise ValueError("identity must be a ProcessIdentity")
    if os.name == "nt":
        if identity.containment_ref is None:
            return ProcessReapResult(ProcessReapStatus.UNSUPPORTED,
                                     "windows_job_object_missing", identity.pid,
                                     identity.process_group_id)
        from .windows_jobs import reap_named_windows_job
        return reap_named_windows_job(identity.containment_ref, pid=identity.pid,
                                      timeout_seconds=timeout_seconds)
    return reap_process_group(identity, grace_seconds=grace_seconds,
                              timeout_seconds=timeout_seconds)


def _result(status: ProcessReapStatus, identity: ProcessIdentity) -> ProcessReapResult:
    return ProcessReapResult(status, "linux_pidfd_process_group", identity.pid,
                             identity.process_group_id)


def _linux_pidfd_supported() -> bool:
    return (os.name == "posix" and Path("/proc/sys/kernel/random/boot_id").is_file()
            and callable(getattr(os, "pidfd_open", None))
            and callable(getattr(signal, "pidfd_send_signal", None))
            and hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT"))


def _linux_process_state(pid: int) -> tuple[str, ProcessIdentity]:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat.rfind(")")
    if close < 0:
        raise OSError("malformed Linux process stat record")
    fields = stat[close + 1:].split()
    if len(fields) <= 19:
        raise OSError("incomplete Linux process stat record")
    state, process_group_id = fields[0], int(fields[2])
    start_ticks = int(fields[19])
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii").strip()
    identity = ProcessIdentity(pid, f"linux:{boot_id}:{start_ticks}", process_group_id)
    return state, identity


def _linux_group_has_live_member(process_group_id: int) -> bool | None:
    """Return active membership; None means `/proc` could not be checked safely."""
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        except PermissionError:
            return None
        except OSError:
            continue
        close = stat.rfind(")")
        if close < 0:
            return None
        fields = stat[close + 1:].split()
        if len(fields) <= 19:
            return None
        try:
            state, group = fields[0], int(fields[2])
        except ValueError:
            return None
        if group == process_group_id and state not in {"Z", "X"}:
            return True
    return False
