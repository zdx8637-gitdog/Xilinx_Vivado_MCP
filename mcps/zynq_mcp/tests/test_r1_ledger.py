"""test_r1_ledger.py — Execution Ledger tests."""
import json, shutil, tempfile
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    LedgerCorruptError, LedgerSchemaError, LedgerInvalidError,
    LedgerInconsistentError, LedgerWorkspaceMismatchError, ChannelBusyError,
)


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp()); g = InstanceGuard(rt, "ws-test"); g.determine_role()
    yield rt, g; g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

@pytest.fixture
def pled(rtg):
    rt, g = rtg; lp = rt / "ledger.json"
    def _i(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
    return ledger_transaction(g, lp, _i), g, lp


class TestLedger:
    def test_write_and_read(self, pled):
        l, g, lp = pled
        assert l.ledger_sequence >= 1

    def test_sequence_increments(self, pled):
        l, g, lp = pled
        s = l.ledger_sequence
        l2 = ledger_transaction(g, lp, lambda x: x)
        assert l2.ledger_sequence == s + 1

    def test_sequence_monotonic(self, pled):
        l, g, lp = pled
        seqs = []
        for _ in range(5):
            l = ledger_transaction(g, lp, lambda x: x)
            seqs.append(l.ledger_sequence)
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 5

    def test_crash_during_tmp_preserves_old(self, pled):
        l, g, lp = pled
        s = l.ledger_sequence
        Path(str(lp) + ".tmp").write_text("{corrupt", encoding="utf-8")
        l2 = ledger_transaction(g, lp, lambda x: x)
        assert l2.ledger_sequence == s + 1

    def test_os_replace_not_rename(self, pled):
        l, g, lp = pled
        ledger_transaction(g, lp, lambda x: x)
        ledger_transaction(g, lp, lambda x: x)
        assert lp.exists()

    def test_corrupt_json_raises(self, rtg):
        rt, g = rtg; lp = rt / "l.json"; lp.write_text("{bad")
        with pytest.raises(LedgerCorruptError):
            ledger_transaction(g, lp, lambda x: x)

    def test_empty_file_raises(self, rtg):
        rt, g = rtg; lp = rt / "l.json"; lp.write_text("")
        with pytest.raises(LedgerCorruptError):
            ledger_transaction(g, lp, lambda x: x)

    def test_schema_mismatch_raises(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        lp.write_text('{"schema_version":"99","ledger_sequence":1}', encoding="utf-8")
        with pytest.raises(LedgerSchemaError):
            ledger_transaction(g, lp, lambda x: x)

    def test_invalid_lane_raises(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        lp.write_text('{"schema_version":"1.0","ledger_sequence":1,"execution_lane":"INVALID"}', encoding="utf-8")
        with pytest.raises(LedgerInvalidError):
            ledger_transaction(g, lp, lambda x: x)

    def test_active_op_idle_inconsistency(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        lp.write_text(json.dumps({"schema_version":"1.0","ledger_sequence":1,"execution_lane":"IDLE",
            "active_operation":{"operation_id":"op-1","status":"RUNNING"}}), encoding="utf-8")
        with pytest.raises(LedgerInconsistentError):
            ledger_transaction(g, lp, lambda x: x)

    def test_shared_read_does_not_bump_sequence(self, pled):
        l, g, lp = pled
        s = l.ledger_sequence
        l2, _ = ledger_read_shared(g, lp)
        assert l2.ledger_sequence == s

    def test_foreign_workspace_rejected(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id="x"; l.workspace_id="ws-OTHER"; l.execution_lane=EXECUTION_LANE_IDLE; return l
        ledger_transaction(g, lp, _init)
        g2 = InstanceGuard(rt, "ws-test"); g2.determine_role()
        with pytest.raises(Exception):
            ledger_read_shared(g2, lp, "ws-test")

    def test_mutable_worker_state(self, pled):
        l, g, lp = pled
        def _set(l): l.worker["state"] = "STARTING"; return l
        l = ledger_transaction(g, lp, _set)
        assert l.worker["state"] == "STARTING"

    def test_concurrent_transaction_one_admitted(self, pled):
        import threading
        l, g, lp = pled
        def _ctx(l): l.context={"current_stage":"PL_BUILD","board_package_revision":"s","expected_board_revision":"s"}; return l
        l = ledger_transaction(g, lp, _ctx)
        barrier = threading.Barrier(2, timeout=5)
        results = []
        def racer(tag):
            def _mut(l):
                if l.active_operation and l.active_operation.get("status") in ("ACCEPTED","RUNNING"):
                    raise ChannelBusyError("CHANNEL_BUSY")
                l.execution_lane = EXECUTION_LANE_BUSY
                l.active_operation = {"operation_id":f"op-{tag}","status":"ACCEPTED"}
                return l
            barrier.wait()
            try:
                ledger_transaction(g, lp, _mut)
                results.append((tag, "admitted"))
            except ChannelBusyError:
                results.append((tag, "busy"))
            except Exception as e:
                results.append((tag, f"error:{type(e).__name__}"))
        t1 = threading.Thread(target=racer, args=("A",)); t2 = threading.Thread(target=racer, args=("B",))
        t1.start(); t2.start(); t1.join(timeout=10); t2.join(timeout=10)
        assert len(results) == 2
        admitted = [r for r in results if r[1]=="admitted"]; busy = [r for r in results if r[1]=="busy"]
        assert len(admitted)==1 and len(busy)==1, f"Results: {results}"
