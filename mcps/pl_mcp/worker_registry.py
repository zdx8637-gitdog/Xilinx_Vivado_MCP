"""
worker_registry.py -- Per-session workers, operations, leases, background tasks.
"""

from __future__ import annotations
import asyncio, logging, subprocess, threading, time, uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("pl_mcp.worker_registry")
DEFAULT_MAX_WORKERS = 2; MAX_TOMBSTONE_OPS = 1000

_LEGAL = {"accepted":{"running","failed"}, "running":{"succeeded","failed"},
          "succeeded":set(), "failed":set()}
_VALID = {"accepted","running","succeeded","failed"}
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime())+f"{int(time.time()*1e6)%1000000:06d}Z"

class MaxWorkersError(Exception): pass
class WorkerBusyError(Exception): pass
class WorkerNotFoundError(Exception): pass
class ReservationError(Exception): pass

@dataclass
class Operation:
    operation_id: str; status: str = "accepted"
    result: Any = None; error_code: Optional[str] = None
    error_message: Optional[str] = None; reason_code: Optional[str] = None
    progress_pct: Optional[int] = None
    created_at: str = field(default_factory=_now_iso); updated_at: str = field(default_factory=_now_iso)
    def transition(self, ns: str) -> None:
        if ns not in _VALID: raise ValueError(f"Invalid: {ns!r}")
        if ns not in _LEGAL.get(self.status,set()): raise ValueError(f"Illegal: {self.status} → {ns!r}")
        self.status = ns; self.updated_at = _now_iso()
    def is_terminal(self) -> bool: return self.status in ("succeeded","failed")
    def to_dict(self) -> dict:
        d = {"operation_id":self.operation_id,"status":self.status,"result":self.result,
             "progress_pct":self.progress_pct,"created_at":self.created_at,"updated_at":self.updated_at}
        if self.status == "failed":
            d["error"] = {"code":self.error_code or "INTERNAL_ERROR",
                          "message":self.error_message or "Operation failed"}
            if self.reason_code: d["error"]["details"] = {"reason_code":self.reason_code}
        return d

class LeaseEntry:
    """Lease with release callback.  Succeeds only when callback succeeds."""
    __slots__ = ("kind","key","release_cb","_released","_release_error")
    def __init__(self, kind, key, release_cb=None):
        self.kind=kind; self.key=key; self.release_cb=release_cb
        self._released=False; self._release_error: Optional[str]=None
    @property
    def released(self) -> bool: return self._released
    async def release(self) -> bool:
        """Execute callback.  Returns True only after callback succeeds.
        Already-released leases skip the callback."""
        if self._released:
            return True
        if self.release_cb is not None:
            try:
                r = self.release_cb(self.kind, self.key)
                if asyncio.iscoroutine(r): await r
            except Exception as e:
                self._release_error = str(e)
                return False
        self._released = True
        return True
    @property
    def release_error(self) -> Optional[str]: return self._release_error

class WorkerEntry:
    __slots__ = ("session_id","owner","pid","in_flight","poisoned",
                 "operations","leases","background_tasks")
    def __init__(self, sid, owner, pid=None):
        self.session_id=sid; self.owner=owner; self.pid=pid
        self.in_flight=False; self.poisoned=False
        self.operations: dict[str,Operation] = {}
        self.leases: list[LeaseEntry] = []
        self.background_tasks: dict[str, asyncio.Task] = {}
    def add_lease(self, kind, key, release_cb=None) -> LeaseEntry:
        e = LeaseEntry(kind, key, release_cb); self.leases.append(e); return e
    def register_task(self, op_id: str, task: asyncio.Task) -> None:
        self.background_tasks[op_id] = task
    def unregister_task(self, op_id: str) -> None:
        self.background_tasks.pop(op_id, None)
    def cancel_all_tasks(self) -> int:
        count = 0
        for op_id, task in list(self.background_tasks.items()):
            if not task.done():
                task.cancel(); count += 1
            self.background_tasks.pop(op_id, None)
        return count

class WorkerRegistry:
    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS):
        self._lock = threading.RLock()
        self._workers: dict[str, WorkerEntry] = {}
        self._reserved: set[str] = set()
        self._starting_count = 0
        self._tombstone: OrderedDict[str, Operation] = OrderedDict()
        self._max_workers = max_workers

    @property
    def active_count(self) -> int:
        with self._lock: return sum(1 for w in self._workers.values() if not w.poisoned)
    @property
    def max_workers(self) -> int: return self._max_workers
    def _available_slots(self) -> int:
        return self._max_workers - self.active_count - len(self._reserved) - self._starting_count

    def reserve_slot(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._reserved: raise ReservationError(f"Slot already reserved for {session_id}")
            if self._available_slots() <= 0: raise MaxWorkersError(f"Max workers ({self._max_workers}) exceeded")
            self._reserved.add(session_id)
    def commit_reservation(self, session_id: str, owner, pid=None) -> WorkerEntry:
        with self._lock:
            if session_id not in self._reserved: raise ReservationError(f"No reservation for {session_id}")
            self._reserved.discard(session_id)
            entry = WorkerEntry(session_id, owner, pid=pid); self._workers[session_id] = entry; return entry
    def release_reservation(self, session_id: str) -> None:
        with self._lock: self._reserved.discard(session_id)

    def get_worker(self, session_id: str) -> Optional[WorkerEntry]:
        with self._lock:
            e = self._workers.get(session_id); return e if (e and not e.poisoned) else None
    def has_worker(self, session_id: str) -> bool: return self.get_worker(session_id) is not None
    def _get_any(self, session_id: str) -> Optional[WorkerEntry]:
        with self._lock: return self._workers.get(session_id)

    def _move_to_tombstone(self, ops: dict[str, Operation]) -> None:
        with self._lock:
            if not ops: return
            for k, v in ops.items(): self._tombstone[k] = v
            while len(self._tombstone) > MAX_TOMBSTONE_OPS: self._tombstone.popitem(last=False)

    def mark_poisoned(self, session_id: str, command_reason: bool = False) -> None:
        with self._lock:
            e = self._workers.get(session_id)
            if not e: return
            e.poisoned = True; e.in_flight = False
            for op in e.operations.values():
                if not op.is_terminal():
                    op.transition("failed")
                    if command_reason:
                        op.error_code="INTERNAL_ERROR"; op.reason_code="OPERATION_OUTCOME_UNKNOWN"
                        op.error_message="Command outcome unknown after worker failure"
                    else:
                        op.error_code="TOOL_ERROR"; op.reason_code="VIVADO_PROCESS_DEAD"
                        op.error_message="Worker process died"
            logger.warning("Worker poisoned: session=%s command_reason=%s", session_id, command_reason)

    async def shutdown_worker_and_tombstone(self, session_id: str) -> dict:
        entry = self._get_any(session_id)
        if entry is None:
            return {"success":True,"operations_cancelled":0,"worker_removed":False,
                    "pid_cleaned":False,"leases_released":0,"error":None}

        # Step 1: cancel all background tasks
        entry.cancel_all_tasks()

        # Step 2: fail non-terminal operations
        ops_cancelled = 0
        with self._lock:
            for op in entry.operations.values():
                if not op.is_terminal():
                    op.transition("failed"); op.error_code="INTERNAL_ERROR"
                    op.reason_code="SESSION_CLOSED"; op.error_message="Session closed"
                    ops_cancelled += 1

        # Step 3: shutdown owner
        pid_cleaned = False; pid = entry.pid or (getattr(entry.owner,'child_pid',None) if entry.owner else None)
        if entry.owner is not None:
            try:
                result = await entry.owner.shutdown()
                pid_cleaned = result.cleaned
                if pid and self.is_pid_alive(pid):
                    pid_cleaned = False  # PID still alive → not cleaned
            except Exception as e:
                pid_cleaned = False
            if not pid_cleaned and pid and pid > 0:
                self.kill_process_tree(pid); await asyncio.sleep(1.5)
                pid_cleaned = not self.is_pid_alive(pid)
            if not pid_cleaned:
                return {"success":False,"operations_cancelled":ops_cancelled,
                        "worker_removed":False,"pid_cleaned":False,"leases_released":0,
                        "error":"Owner cleanup incomplete; PID still alive"}

        # Step 4: release Project then JTAG leases
        project_leases = [l for l in entry.leases if l.kind=="project"]
        jtag_leases    = [l for l in entry.leases if l.kind=="jtag"]
        other_leases   = [l for l in entry.leases if l.kind not in ("project","jtag")]
        failures = []
        for l in project_leases:
            if not await l.release(): failures.append(f"{l.kind}:{l.key}")
        for l in jtag_leases:
            if not await l.release(): failures.append(f"{l.kind}:{l.key}")
        for l in other_leases:
            if not await l.release(): failures.append(f"{l.kind}:{l.key}")
        if failures:
            return {"success":False,"operations_cancelled":ops_cancelled,
                    "worker_removed":False,"pid_cleaned":pid_cleaned,"leases_released":0,
                    "error":f"Lease release failed: {'; '.join(failures)}"}

        with self._lock:
            if entry.operations: self._move_to_tombstone(dict(entry.operations))
            self._workers.pop(session_id, None)

        released = sum(1 for l in entry.leases if l.released)
        return {"success":True,"operations_cancelled":ops_cancelled,"worker_removed":True,
                "pid_cleaned":pid_cleaned,"leases_released":released,"error":None}

    def acquire_in_flight(self, session_id: str) -> None:
        with self._lock:
            e = self._workers.get(session_id)
            if not e or e.poisoned: raise WorkerNotFoundError(f"No worker: {session_id}")
            if e.in_flight: raise WorkerBusyError(f"Worker busy: {session_id}")
            e.in_flight = True
    def release_in_flight(self, session_id: str) -> None:
        with self._lock:
            e = self._workers.get(session_id)
            if e: e.in_flight = False

    def create_operation(self, session_id: str) -> Operation:
        with self._lock:
            e = self._workers.get(session_id)
            if not e or e.poisoned: raise WorkerNotFoundError(f"No worker: {session_id}")
            op = Operation(operation_id=f"op-{uuid.uuid4().hex}")
            e.operations[op.operation_id] = op; return op

    def get_operation(self, operation_id: str) -> Optional[Operation]:
        with self._lock:
            for e in self._workers.values():
                op = e.operations.get(operation_id)
                if op is not None: return op
            return self._tombstone.get(operation_id)

    def update_operation(self, operation_id: str, **kwargs) -> Optional[Operation]:
        op = self.get_operation(operation_id)
        if op is None: return None
        with self._lock:
            if "status" in kwargs: ns=kwargs.pop("status"); op.transition(ns)
            for k,v in kwargs.items():
                if hasattr(op,k): setattr(op,k,v)
            op.updated_at=_now_iso()
        return op

    def register_task(self, session_id: str, op_id: str, task: asyncio.Task) -> None:
        with self._lock:
            e = self._workers.get(session_id)
            if e: e.register_task(op_id, task)
    def unregister_task(self, session_id: str, op_id: str) -> None:
        with self._lock:
            e = self._workers.get(session_id)
            if e: e.unregister_task(op_id)
    def task_count(self, session_id: str) -> int:
        with self._lock:
            e = self._workers.get(session_id)
            return len(e.background_tasks) if e else 0

    def tombstone_count(self) -> int:
        with self._lock: return len(self._tombstone)

    @staticmethod
    def kill_process_tree(pid: int) -> bool:
        if not pid or pid<=0: return False
        try: r=subprocess.run(["taskkill","/PID",str(pid),"/T","/F"],capture_output=True,timeout=15)
        except Exception: return False
        return r.returncode in (0,128)
    @staticmethod
    def is_pid_alive(pid: int) -> bool:
        if not pid or pid<=0: return False
        try:
            r=subprocess.run(["tasklist","/FI",f"PID eq {pid}","/NH"],capture_output=True,text=True,timeout=5)
            return str(pid) in r.stdout and "No tasks" not in r.stdout
        except Exception: return False

_registry: Optional[WorkerRegistry] = None
def get_registry() -> WorkerRegistry:
    global _registry
    if _registry is None: _registry = WorkerRegistry()
    return _registry
def reset_registry() -> None:
    global _registry; _registry = WorkerRegistry()
