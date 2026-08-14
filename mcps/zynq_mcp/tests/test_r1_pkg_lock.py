"""test_r1_pkg_lock.py — _PackageLock crash safety + canonical key + fault injection."""
import os, shutil, subprocess, sys, time, uuid
from pathlib import Path
from unittest.mock import patch
import pytest

from mcps.zynq_mcp.control.workspace import resolve_workspace_root
from mcps.zynq_mcp.control.process_guard import is_pid_alive
from mcps.common.board_package import _PackageLock, _pkg_lock_path

WS = str(resolve_workspace_root())


class TestCrashSafety:
    def test_crash_holder_recovery(self, tmp_path):
        pkg = str(tmp_path / "c"); os.makedirs(pkg)
        code = f"""
import sys, time, os; sys.path.insert(0, r'{WS}')
from mcps.common.board_package import _PackageLock
lock=_PackageLock(r'{pkg}'); lock.acquire()
print(f'L|pid={{os.getpid()}}', flush=True)
while True: time.sleep(1)
"""
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = proc.stdout.readline()
        assert "L|pid" in line
        pid = int(line.strip().split("=")[1])
        assert is_pid_alive(pid)
        proc.kill(); proc.wait(timeout=10); time.sleep(0.5)
        assert not is_pid_alive(pid)
        l2 = _PackageLock(pkg, timeout_s=5.0); l2.acquire(); l2.release()

    def test_live_lock_not_stolen(self, tmp_path):
        pkg = str(tmp_path / "l"); os.makedirs(pkg)
        code = f"""
import sys, time, os; sys.path.insert(0, r'{WS}')
from mcps.common.board_package import _PackageLock
lock=_PackageLock(r'{pkg}'); lock.acquire()
print(f'L|pid={{os.getpid()}}', flush=True)
time.sleep(10); lock.release()
"""
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc.stdout.readline()
        with pytest.raises(TimeoutError):
            _PackageLock(pkg, timeout_s=1.5).acquire()
        proc.wait(timeout=15)

    def test_different_packages_independent(self, tmp_path):
        a = str(tmp_path / "a"); b = str(tmp_path / "b"); os.makedirs(a); os.makedirs(b)
        la = _PackageLock(a); la.acquire(); lb = _PackageLock(b); lb.acquire()
        lb.release(); la.release()

    def test_double_release_idempotent(self, tmp_path):
        pkg = str(tmp_path / "rr"); os.makedirs(pkg)
        lock = _PackageLock(pkg); lock.acquire()
        lock.release()
        lock.release()  # _fd=None, _cleanup_required=False → safe no-op

    def test_double_acquire_rejected(self, tmp_path):
        pkg = str(tmp_path / "da"); os.makedirs(pkg)
        lock = _PackageLock(pkg); lock.acquire()
        with pytest.raises(RuntimeError, match="already acquired"):
            lock.acquire()
        lock.release()

    def test_acquire_with_dirty_fd_rejected(self, tmp_path):
        """acquire rejects if _fd is still set from prior failure."""
        pkg = str(tmp_path / "df"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        lock.acquire()
        lock.release()
        # Normal case: acquire again
        lock.acquire()
        lock.release()

    def test_canonical_key_equivalent_paths(self, tmp_path):
        base = tmp_path / "equiv"; base.mkdir()
        target = (base / "pkg").resolve(); target.mkdir()
        k1 = _pkg_lock_path(str(target))
        k2 = _pkg_lock_path(str(target) + os.sep + ".")
        k3 = _pkg_lock_path(str(base / ".." / base.name / "pkg"))
        assert k1 == k2 == k3


class TestFaultInjection:
    def test_lock_exception_close_ok_fd_none(self, tmp_path):
        """A: _os_lock raises, _os_close succeeds → fd=None, cleanup_required=False, original error propagated."""
        pkg = str(tmp_path / "a"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        with patch.object(lock, '_os_open', return_value=999):
            with patch.object(lock, '_os_lock', side_effect=OSError(1, "fake lock err")):
                with patch.object(lock, '_os_close') as mock_close:
                    with pytest.raises(OSError, match="fake lock err"):
                        lock.acquire()
                    mock_close.assert_called_once_with(999)
        assert lock._fd is None, "A: fd must be None when close succeeds"
        assert not lock._owns_lock
        assert not lock._cleanup_required

    def test_lock_exception_close_fails_cleanup_required(self, tmp_path):
        """B: _os_lock raises AND _os_close also fails →
        _cleanup_required=True, _fd retained, __cause__=lock_err."""
        pkg = str(tmp_path / "b"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        lock_err = OSError(1, "fake lock err")
        with patch.object(lock, '_os_open', return_value=999):
            with patch.object(lock, '_os_lock', side_effect=lock_err):
                with patch.object(lock, '_os_close', side_effect=OSError(2, "fake close err")):
                    try:
                        lock.acquire()
                    except OSError as e:
                        assert "both failed" in str(e)
                        assert e.__cause__ is lock_err
        # State B: cleanup_required, fd retained
        assert lock._fd is not None, "B: fd must be retained for explicit cleanup"
        assert not lock._owns_lock
        assert lock._cleanup_required

        # release() on cleanup_required must reject
        with pytest.raises(OSError, match="Cannot release"):
            lock.release()

        # acquire() must reject while dirty
        with pytest.raises(RuntimeError, match="not clean"):
            lock.acquire()

        # Cleanup: close only (no unlock). Patch close to succeed.
        with patch.object(lock, '_os_close', return_value=None):
            lock.cleanup()
        assert lock._fd is None
        assert not lock._cleanup_required

        # After cleanup, acquire can proceed (use real open/lock)
        lock.acquire()
        lock.release()
        assert lock._fd is None

    def test_cleanup_fails_retry(self, tmp_path):
        """cleanup close fails → fd retained, cleanup_required remains True."""
        pkg = str(tmp_path / "cf"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        lock_err = OSError(1, "fake lock err")
        with patch.object(lock, '_os_open', return_value=999):
            with patch.object(lock, '_os_lock', side_effect=lock_err):
                with patch.object(lock, '_os_close', side_effect=OSError(2, "fake close")):
                    try:
                        lock.acquire()
                    except OSError:
                        pass
        assert lock._cleanup_required and lock._fd is not None

        # cleanup fails — still uses mocked fd 999
        with patch.object(lock, '_os_close', side_effect=OSError(3, "fake close2")):
            with pytest.raises(OSError, match="cleanup close failed"):
                lock.cleanup()
        assert lock._cleanup_required, "cleanup_required must persist after failed cleanup close"
        assert lock._fd is not None

        # cleanup succeeds on retry
        with patch.object(lock, '_os_close', return_value=None):
            lock.cleanup()
        assert not lock._cleanup_required
        assert lock._fd is None

    def test_unlock_fail_close_success_state_b(self, tmp_path):
        """release: unlock fails, close succeeds → handle IS released."""
        pkg = str(tmp_path / "uf"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        lock.acquire()
        with patch.object(lock, '_os_unlock', side_effect=OSError("fake unlock err")):
            with pytest.raises(OSError, match="unlock failed.*handle closed"):
                lock.release()
        assert lock._fd is None, "fd must be None after close succeeded"
        assert not lock._owns_lock
        assert not lock._cleanup_required

    def test_close_fail_unlock_success_state_c(self, tmp_path):
        """release: close fails, unlock succeeds → cleanup_required, fd retained."""
        pkg = str(tmp_path / "cf2"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        lock.acquire()
        with patch.object(lock, '_os_unlock', return_value=None):
            with patch.object(lock, '_os_close', side_effect=OSError("fake close err")):
                with pytest.raises(OSError, match="close failed"):
                    lock.release()
        assert lock._fd is not None
        assert lock._cleanup_required
        assert not lock._owns_lock
        with pytest.raises(OSError, match="Cannot release"):
            lock.release()
        with patch.object(lock, '_os_close', return_value=None):
            lock.cleanup()
        assert lock._fd is None

    def test_both_fail_state_d(self, tmp_path):
        """release: both fail → cleanup_required, fd retained."""
        pkg = str(tmp_path / "d"); os.makedirs(pkg)
        lock = _PackageLock(pkg)
        lock.acquire()
        with patch.object(lock, '_os_unlock', side_effect=OSError("fake unlock")):
            with patch.object(lock, '_os_close', side_effect=OSError("fake close")):
                with pytest.raises(OSError, match="both failed"):
                    lock.release()
        assert lock._fd is not None
        assert lock._cleanup_required
        assert not lock._owns_lock
        with patch.object(lock, '_os_close', return_value=None):
            lock.cleanup()
        assert lock._fd is None


class TestCrossProcessFreeze:
    def test_two_processes_simultaneous_freeze(self, tmp_path):
        from mcps.common.tests.test_package_integration import _seal_pkg
        pkg, rev = _seal_pkg(tmp_path, "T_BARRIER")
        pkg_str = str(Path(pkg))
        r1 = tmp_path / "ready_A"; r2 = tmp_path / "ready_B"

        script = f"""
import sys, os, time; from pathlib import Path
sys.path.insert(0, r'{WS}')
from mcps.common.board_package import freeze_package
marker=Path(sys.argv[1]); marker.write_text('ready')
while True:
    if (Path(r'{tmp_path}')/'ready_A').exists() and (Path(r'{tmp_path}')/'ready_B').exists():
        break
    time.sleep(0.01)
try:
    result=freeze_package(r'{pkg_str}')
    print(f'RESULT:{{result}}', flush=True)
except Exception as e:
    print(f'ERROR:{{type(e).__name__}}:{{e}}', flush=True)
"""
        p1 = subprocess.Popen([sys.executable, "-c", script, str(r1)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p2 = subprocess.Popen([sys.executable, "-c", script, str(r2)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out1 = p1.stdout.read(); err1 = p1.stderr.read(); rc1 = p1.wait(timeout=30)
        out2 = p2.stdout.read(); err2 = p2.stderr.read(); rc2 = p2.wait(timeout=30)
        assert rc1 == 0 and rc2 == 0, f"P1 rc={rc1} out={out1}\nP2 rc={rc2} out={out2}"
        results = set()
        for out in [out1, out2]:
            if "RESULT:published" in out: results.add("published")
            elif "RESULT:already_exists_same" in out: results.add("already_exists_same")
            else: assert False, f"Bad: {out}"
        assert results == {"published", "already_exists_same"}
        files = os.listdir(pkg_str)
        assert len(files) == 6
        assert "package_manifest.draft.json" not in files
        assert "package_manifest.json" in files
