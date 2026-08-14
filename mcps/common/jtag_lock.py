"""
jtag_lock.py — JTAG lock wrapper.

Delegates to project_lock jtag_acquire/jtag_acquire_read for OS-level locking.
"""

from mcps.common.project_lock import (
    jtag_acquire, jtag_acquire_read,
    release, heartbeat,
    set_lock_dir,
    Lease, LockAcquireResult,
)


def acquire(hw_server_url: str, cable_serial: str, owner: str,
            ttl_s: int = 300, wait_s: float = 0) -> LockAcquireResult:
    return jtag_acquire(hw_server_url, cable_serial, owner,
                        ttl_s=ttl_s, wait_s=wait_s)


def acquire_read(hw_server_url: str, cable_serial: str,
                 owner: str = "") -> LockAcquireResult:
    return jtag_acquire_read(hw_server_url, cable_serial, owner)
