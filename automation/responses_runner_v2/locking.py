from __future__ import annotations

import errno
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class RunLockError(RuntimeError):
    """Raised when a run lock cannot be acquired within the requested timeout."""


@contextmanager
def run_lock(
    run_dir: Path,
    *,
    timeout_seconds: float = 0.0,
    poll_interval_seconds: float = 0.05,
) -> Iterator[Path]:
    """Hold an exclusive, process-level lock for one run directory.

    A zero timeout is fail-fast. Positive timeouts use short bounded polling so
    callers can distinguish a clean lock conflict from API or artifact errors.
    """

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(run_dir, 0o700)
    lock_path = run_dir / ".runner.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise RunLockError(f"Run is locked by another process: {run_dir}") from exc
                time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))
        yield lock_path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
