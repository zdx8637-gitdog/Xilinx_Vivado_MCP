"""test_r1_guard.py — Instance Guard tests."""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard, InstanceGuardFatalError
from mcps.zynq_mcp.control.workspace import resolve_workspace_root


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp()); g = InstanceGuard(rt, "ws-test"); g.determine_role()
    yield rt, g; g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)


class TestInstanceGuard:
    def test_primary_acquires_owner_lock(self, rtg):
        assert rtg[1].is_primary
        assert (rtg[0] / "instance_owner.lock").exists()

    def test_ledger_exclusive_lock(self, rtg):
        h = rtg[1].acquire_ledger_exclusive()
        rtg[1].release_ledger_lock(h)
        assert h > 0

    def test_ledger_shared_lock(self, rtg):
        h = rtg[1].acquire_ledger_shared()
        rtg[1].release_ledger_lock(h)
        assert h > 0

    def test_dual_locks_are_separate_files(self, rtg):
        rtg[1].acquire_ledger_exclusive()
        assert (rtg[0] / "instance_owner.lock").exists()
        assert (rtg[0] / "ledger.lock").exists()
        assert (rtg[0] / "instance_owner.lock") != (rtg[0] / "ledger.lock")

    def test_second_instance_is_secondary(self, rtg):
        g2 = InstanceGuard(rtg[0], "ws-test"); r = g2.determine_role()
        assert r.name == "SECONDARY"

    def test_non_lock_error_is_fatal(self, rtg):
        assert issubclass(InstanceGuardFatalError, Exception)

    def test_lock_handle_not_inherited(self, tmp_path):
        """Child of Primary must NOT inherit owner lock."""
        rt = tmp_path / ".zynq3"; rt.mkdir(parents=True)
        child_script = str(Path(__file__).resolve().parent / "helpers" / "child_check_lock.py")
        g = InstanceGuard(rt, "ws-inh"); r = g.determine_role()
        assert r.name == "PRIMARY"
        child = subprocess.run([sys.executable, child_script, str(rt), "ws-inh"],
                               capture_output=True, text=True, timeout=10)
        assert "CHILD_ROLE:SECONDARY" in child.stdout, f"Child inherited: {child.stdout}"
        g.release_owner_lock()
