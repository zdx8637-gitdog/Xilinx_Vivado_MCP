"""
operation_registry.py — Ledger-persisted operation registry.
Memory dict is cache only. Source of truth = ledger.active_operation / previous_operation / dedup_registry.
"""
import asyncio, threading, uuid, hashlib, json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from mcps.zynq_mcp.control.execution_ledger import (
    _now_iso, ExecutionLedger,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED, OP_CANCELLED,
    OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TERMINAL, OP_NON_TERMINAL,
)

_VALID = frozenset([OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED,
    OP_CANCELLED, OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN])
_LEGAL = {OP_ACCEPTED: frozenset([OP_RUNNING, OP_FAILED, OP_CANCELLED, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN]),
    OP_RUNNING: frozenset([OP_SUCCEEDED, OP_FAILED, OP_CANCELLED, OP_TIMED_OUT,
                            OP_INTERRUPTED, OP_OUTCOME_UNKNOWN])}
for s in OP_TERMINAL: _LEGAL[s] = frozenset()
MAX_TOMBSTONE = 1000


@dataclass
class Operation:
    operation_id: str; tool_name: str = ""; status: str = OP_ACCEPTED
    result: Any = None; error_code: Optional[str] = None; error_message: Optional[str] = None
    reason_code: Optional[str] = None; progress_pct: Optional[int] = None
    created_at: str = field(default_factory=_now_iso); updated_at: str = field(default_factory=_now_iso)
    deduplicated: bool = False; api_category: str = ""; request_signature: str = ""
    observation: dict[str, Any] = field(default_factory=dict)
    artifact_state: str = "NOT_APPLICABLE"
    deadline_at: Optional[str] = None
    recommended_action: str = "NONE"

    def transition(self, ns):
        if ns not in _VALID: raise ValueError(f"Invalid: {ns!r}")
        if ns not in _LEGAL.get(self.status, frozenset()): raise ValueError(f"Illegal: {self.status} → {ns!r}")
        self.status = ns; self.updated_at = _now_iso()
    def is_terminal(self): return self.status in OP_TERMINAL

    def to_dict(self):
        d = {"operation_id": self.operation_id, "tool_name": self.tool_name, "status": self.status,
            "result": self.result, "progress_pct": self.progress_pct, "created_at": self.created_at,
            "updated_at": self.updated_at, "deduplicated": self.deduplicated, "api_category": self.api_category,
            "request_signature": self.request_signature,
            "observation": dict(self.observation), "artifact_state": self.artifact_state,
            "deadline_at": self.deadline_at, "recommended_action": self.recommended_action}
        if self.status in (OP_FAILED, OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_CANCELLED):
            d["error"] = {"code": self.error_code or "INTERNAL_ERROR", "message": self.error_message or ""}
            if self.reason_code: d["error"]["details"] = {"reason_code": self.reason_code}
        return d

    @classmethod
    def from_dict(cls, d):
        err = d.get("error", {}); err = err if isinstance(err, dict) else {}
        return cls(operation_id=d.get("operation_id", ""), tool_name=d.get("tool_name", ""),
            status=d.get("status", OP_ACCEPTED), result=d.get("result"),
            error_code=err.get("code"), error_message=err.get("message"),
            reason_code=err.get("details", {}).get("reason_code") if isinstance(err.get("details"), dict) else None,
            progress_pct=d.get("progress_pct"), created_at=d.get("created_at", _now_iso()),
            updated_at=d.get("updated_at", _now_iso()), deduplicated=d.get("deduplicated", False),
            api_category=d.get("api_category", ""), request_signature=d.get("request_signature", ""),
            observation=dict(d.get("observation") or {}),
            artifact_state=d.get("artifact_state", "NOT_APPLICABLE"),
            deadline_at=d.get("deadline_at"),
            recommended_action=d.get("recommended_action", "NONE"))


class OperationRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._ops: dict[str, Operation] = {}
        self._tombstone: OrderedDict[str, Operation] = OrderedDict()
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._signatures: dict[str, str] = {}   # signature → operation_id

    def restore_from_ledger(self, ledger):
        """Recover operations + dedup signatures from ledger after MCP restart."""
        with self._lock:
            ao = ledger.active_operation
            if ao and ao.get("operation_id"):
                op = Operation.from_dict(ao); self._ops[op.operation_id] = op
                sig = ao.get("request_signature", "")
                if sig: self._signatures[sig] = op.operation_id
            po = ledger.previous_operation
            if po and po.get("operation_id"):
                op = Operation.from_dict(po)
                if op.is_terminal(): self._tombstone[op.operation_id] = op
                else: self._ops[op.operation_id] = op
                sig = po.get("request_signature", "")
                if sig: self._signatures[sig] = op.operation_id
            dr = ledger.dedup_registry or {}
            for sig, oid in dr.items():
                if sig not in self._signatures:
                    self._signatures[sig] = oid

    def create(self, tool_name="", api_category=""):
        op_id = f"op-{uuid.uuid4().hex}"
        op = Operation(operation_id=op_id, tool_name=tool_name, api_category=api_category)
        with self._lock: self._ops[op_id] = op
        return op

    def get(self, operation_id):
        with self._lock:
            op = self._ops.get(operation_id)
            return op if op else self._tombstone.get(operation_id)

    def find_duplicate(self, signature):
        with self._lock:
            existing_id = self._signatures.get(signature)
            if existing_id: return self.get(existing_id)
            return None

    def register_signature(self, signature, operation_id):
        with self._lock: self._signatures[signature] = operation_id

    def transition(self, operation_id, new_status, **kwargs):
        op = self.get(operation_id)
        if op is None: return None
        with self._lock:
            try: op.transition(new_status)
            except ValueError: return None
            for k, v in kwargs.items():
                if hasattr(op, k): setattr(op, k, v)
            op.updated_at = _now_iso()
        return op

    def register_task(self, operation_id, task):
        with self._lock: self._background_tasks[operation_id] = task
    def unregister_task(self, operation_id):
        with self._lock: self._background_tasks.pop(operation_id, None)

    def admit_cache(self, operation_id, tool_name, status, signature=""):
        """Create in-memory Operation entry. Thread-safe."""
        op = Operation(operation_id=operation_id, tool_name=tool_name,
                       status=status, request_signature=signature)
        with self._lock: self._ops[operation_id] = op
        if signature:
            self._signatures[signature] = operation_id
        return op

    def remove_cache(self, operation_id):
        """Remove in-memory Operation entry."""
        with self._lock: self._ops.pop(operation_id, None)

    def has_task(self, operation_id) -> bool:
        with self._lock: return operation_id in self._background_tasks

    def task_count(self) -> int:
        with self._lock: return len(self._background_tasks)

    async def shutdown_tasks(self):
        """Cancel + await all background tasks. For server finalizer.
        Does NOT cancel running ops — writes INTERRUPTED."""
        tasks = []
        with self._lock:
            tasks = list(self._background_tasks.items())
        for oid, task in tasks:
            if not task.done():
                task.cancel()
                try: await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError): pass

    def move_to_tombstone(self, operation):
        with self._lock:
            self._ops.pop(operation.operation_id, None)
            self._tombstone[operation.operation_id] = operation
            while len(self._tombstone) > MAX_TOMBSTONE: self._tombstone.popitem(last=False)

    def tombstone_count(self):
        with self._lock: return len(self._tombstone)


def request_fingerprint(session_id, stage, tool_name, args, artifact_revision):
    canonical = json.dumps({"sid": session_id, "stage": stage, "tool": tool_name,
        "args": dict(sorted(args.items())), "rev": artifact_revision}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
