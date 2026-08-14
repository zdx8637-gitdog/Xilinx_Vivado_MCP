"""T-B02-008: Project Lock — re-entrancy, thread safety, token validation,
exception-safe release, heartbeat/release serialization."""

import json, os, subprocess, sys, threading, time
from pathlib import Path

import pytest
from mcps.common.project_lock import (
    acquire, acquire_read, release, heartbeat, set_lock_dir,
    Lease, LockAcquireResult, _project_canonical_key, _PROJECT_PREFIX,
    _active, _pending, _lock,
)

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
assert Path(PROJECT_ROOT).name == "fpgaproject"


@pytest.fixture(autouse=True)
def _lock_dir(tmp_path):
    import mcps.common.project_lock as _pl
    d = str(tmp_path / "locks")
    with _pl._lock:
        _pl._active.clear()
        _pl._pending.clear()
    _pl.set_lock_dir(d)
    yield
    with _pl._lock:
        _pl._active.clear()
        _pl._pending.clear()
    _pl._lock_dir = None


# === Validation ===

def test_empty_key():
    with pytest.raises(ValueError, match="non-empty"):
        acquire(" ", owner="T", ttl_s=10)


def test_empty_owner():
    with pytest.raises(ValueError, match="non-empty"):
        acquire("proj", owner="  ", ttl_s=10)


def test_bool_ttl():
    with pytest.raises(ValueError):
        acquire("proj", owner="T", ttl_s=True)  # type: ignore


def test_nan_wait():
    with pytest.raises(ValueError, match="finite"):
        acquire("proj", owner="T", ttl_s=10, wait_s=float('nan'))


def test_negative_wait():
    with pytest.raises(ValueError, match=">= 0"):
        acquire("proj", owner="T", ttl_s=10, wait_s=-1)


def test_bool_wait():
    with pytest.raises(ValueError, match="number"):
        acquire("proj", owner="T", ttl_s=10, wait_s=True)  # type: ignore


# === Basic lifecycle ===

def test_acquire_release():
    r = acquire("proj_a", owner="T1", ttl_s=10)
    assert r.status == "acquired"; release(r.lease)


def test_reacquire():
    r = acquire("proj_b", owner="T1", ttl_s=10); release(r.lease)
    r2 = acquire("proj_b", owner="T2", ttl_s=10)
    assert r2.status == "acquired"; release(r2.lease)


def test_different_keys():
    r1 = acquire("p1", owner="A", ttl_s=10); r2 = acquire("p2", owner="B", ttl_s=10)
    assert r1.status == r2.status == "acquired"
    release(r1.lease); release(r2.lease)


# === Re-entrancy: same owner + same resource ===

def test_write_write_same_owner_busy():
    r1 = acquire("re_w", owner="SAME", ttl_s=10)
    r2 = acquire("re_w", owner="SAME", ttl_s=5, wait_s=0)
    assert r2.status == "busy"; release(r1.lease)


def test_write_read_same_owner_busy():
    r1 = acquire("re_wr", owner="SAME", ttl_s=10)
    r2 = acquire_read("re_wr", owner="SAME")
    assert r2.status == "busy"; release(r1.lease)


def test_read_write_same_owner_busy():
    r1 = acquire_read("re_rw", owner="SAME")
    r2 = acquire("re_rw", owner="SAME", ttl_s=5, wait_s=0)
    assert r2.status == "busy"; release(r1.lease)


def test_read_read_same_owner_busy():
    r1 = acquire_read("re_rr", owner="SAME")
    r2 = acquire_read("re_rr", owner="SAME")
    assert r2.status == "busy"; release(r1.lease)


# === Different owners: read/read coexists, write blocked ===

def test_read_read_different_owners_coexist():
    r1 = acquire_read("coexist", owner="R1")
    r2 = acquire_read("coexist", owner="R2")
    assert r1.status == r2.status == "acquired"
    release(r1.lease); release(r2.lease)


def test_read_write_different_owners_blocked():
    r1 = acquire_read("rw_diff", owner="R1")
    w = acquire("rw_diff", owner="W", ttl_s=5, wait_s=0)
    assert w.status == "busy"; release(r1.lease)


def test_write_read_different_owners_blocked():
    w = acquire("wr_diff", owner="W", ttl_s=10)
    r = acquire_read("wr_diff", owner="R1")
    assert r.status == "busy"; release(w.lease)


# === Heartbeat ===

def test_heartbeat():
    r = acquire("hb", owner="T", ttl_s=10)
    old = r.lease.heartbeat_at
    new = heartbeat(r.lease)
    assert new.heartbeat_at > old; release(r.lease)


def test_heartbeat_read_raises():
    r = acquire_read("hbr", owner="T")
    with pytest.raises(RuntimeError, match="read lease"):
        heartbeat(r.lease)
    release(r.lease)


# === Heartbeat/release serialization ===

def test_heartbeat_release_serialization(monkeypatch):
    """Heartbeat holds registry RLock through _write_meta → release blocks.

    Correct sequence:
      1. heartbeat starts, enters _lock, pauses in _write_meta
      2. release starts, blocks on _lock (heartbeat still holds it)
      3. Release thread is alive but not completed
      4. Heartbeat unblocked → finishes _write_meta → releases _lock → done
      5. Release acquires _lock → marks releasing → deletes meta → releases OS lock → done
      6. Metadata file must not exist BEFORE contender acquire
      7. Old lease must not be in _active BEFORE contender acquire
      8. Contender succeeds
    """
    from mcps.common.project_lock import _write_meta, _read_meta
    r = acquire("hb_rel", owner="T", ttl_s=10)

    hb_entered = threading.Event()
    hb_unblock = threading.Event()
    orig_write = _write_meta

    def slow_write(path, lease):
        hb_entered.set()
        assert hb_unblock.wait(timeout=5), "heartbeat unblock never fired"
        orig_write(path, lease)

    monkeypatch.setattr("mcps.common.project_lock._write_meta", slow_write)

    hb_result = []
    rel_result = []

    def do_heartbeat():
        try:
            new = heartbeat(r.lease)
            hb_result.append(("ok", new.heartbeat_at))
        except Exception as e:
            hb_result.append(("err", str(e)))

    def do_release():
        try:
            release(r.lease)
            rel_result.append("released")
        except Exception as e:
            rel_result.append(f"error: {e}")

    t_hb = threading.Thread(target=do_heartbeat)
    t_hb.start()
    assert hb_entered.wait(timeout=5), "heartbeat never entered _write_meta"

    # Start release — it will block on _lock
    t_rel = threading.Thread(target=do_release)
    t_rel.start()
    time.sleep(0.1)

    # Heartbeat still holds _lock → release must NOT be done yet
    assert not rel_result, f"Release completed before heartbeat: {rel_result}"
    assert t_rel.is_alive(), "Release thread must still be alive (blocked on lock)"

    # Unblock heartbeat → it finishes _write_meta → releases _lock
    hb_unblock.set()
    t_hb.join(timeout=5)
    assert hb_result[0][0] == "ok", f"heartbeat: {hb_result}"

    # Now release can proceed
    t_rel.join(timeout=5)
    assert "released" in rel_result, f"release: {rel_result}"

    # Before contender: metadata must NOT exist
    import mcps.common.project_lock as _pl
    lpath = _pl._lock_file_path(_pl._canonical_hash(r.lease.lock_key))
    assert not os.path.exists(lpath + ".meta"), "metadata must not exist before contender"

    # Old lease must not be in _active
    with _lock:
        assert r.lease.lease_id not in _active

    # Contender succeeds
    r2 = acquire("hb_rel", owner="R", ttl_s=10)
    assert r2.status == "acquired"
    release(r2.lease)


def test_heartbeat_to_releasing_lease_rejected(monkeypatch):
    """After release marks 'releasing', heartbeat must be rejected."""
    from mcps.common.project_lock import _del_meta as orig_del
    r = acquire("hb_mid_rel", owner="T", ttl_s=10)

    rel_entered = threading.Event()
    rel_unblock = threading.Event()

    def slow_del(path):
        rel_entered.set()
        rel_unblock.wait(timeout=5)
        orig_del(path)

    monkeypatch.setattr("mcps.common.project_lock._del_meta", slow_del)

    hb_res = []

    def do_hb():
        assert rel_entered.wait(timeout=5), "release never started"
        time.sleep(0.05)
        try:
            heartbeat(r.lease)
            hb_res.append("ok")
        except RuntimeError as e:
            hb_res.append(f"rejected: {e}")

    def do_rel():
        try:
            release(r.lease)
        except Exception:
            pass

    t_hb = threading.Thread(target=do_hb)
    t_rel = threading.Thread(target=do_rel)
    t_hb.start(); t_rel.start()
    t_rel.join(timeout=5)
    rel_unblock.set()
    t_hb.join(timeout=5)

    assert hb_res, f"heartbeat thread had no result"
    assert "rejected" in hb_res[0], f"Expected rejection, got {hb_res}"

    r2 = acquire("hb_mid_rel", owner="R", ttl_s=10)
    assert r2.status == "acquired"
    release(r2.lease)
    with _lock:
        assert r.lease.lease_id not in _active


# === Token validation: forged fields rejected, lock still held ===

def _make_forged(base: Lease, **overrides) -> Lease:
    d = {f: getattr(base, f) for f in base.__dataclass_fields__}
    d.update(overrides)
    return Lease(**d)


def test_wrong_owner_release_rejected():
    r = acquire("t_owner", owner="REAL", ttl_s=10)
    fake = _make_forged(r.lease, owner_session_id="FAKE")
    with pytest.raises(RuntimeError, match="owner_session_id"):
        release(fake)
    assert acquire("t_owner", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r.lease)


def test_wrong_pid_release_rejected():
    r = acquire("t_pid", owner="T", ttl_s=10)
    fake = _make_forged(r.lease, pid=99999)
    with pytest.raises(RuntimeError, match="pid"):
        release(fake)
    assert acquire("t_pid", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r.lease)


def test_wrong_pid_heartbeat_rejected():
    r = acquire("t_hb_pid", owner="T", ttl_s=10)
    fake = _make_forged(r.lease, pid=99999)
    with pytest.raises(RuntimeError, match="pid"):
        heartbeat(fake)
    release(r.lease)


def test_wrong_mode_release_rejected():
    r = acquire("t_mode", owner="T", ttl_s=10)
    fake = _make_forged(r.lease, mode="read", ttl_s=0)
    with pytest.raises(RuntimeError, match="mode"):
        release(fake)
    assert acquire("t_mode", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r.lease)


def test_wrong_mode_heartbeat_rejected():
    r = acquire("t_hb_mode", owner="T", ttl_s=10)
    fake = _make_forged(r.lease, mode="read", ttl_s=0)
    with pytest.raises(RuntimeError, match="mode"):
        heartbeat(fake)
    release(r.lease)


def test_wrong_ttl_release_rejected():
    r = acquire("t_ttl", owner="T", ttl_s=10)
    fake = _make_forged(r.lease, ttl_s=999)
    with pytest.raises(RuntimeError, match="ttl_s"):
        release(fake)
    assert acquire("t_ttl", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r.lease)


def test_wrong_acquired_at_release_rejected():
    r = acquire("t_acq", owner="T", ttl_s=10)
    fake = _make_forged(r.lease, acquired_at="2020-01-01T00:00:00Z")
    with pytest.raises(RuntimeError, match="acquired_at"):
        release(fake)
    release(r.lease)


def test_double_release():
    r = acquire("t_dbl", owner="T", ttl_s=10)
    release(r.lease)
    with pytest.raises(RuntimeError, match="not found"):
        release(r.lease)


def test_unknown_lease():
    with pytest.raises(RuntimeError, match="not found"):
        release(Lease(
            lease_id="fake", owner_session_id="X", lock_key="k",
            scope="s", mode="write", ttl_s=10,
            acquired_at="2024-01-01T00:00:00.000000Z",
            heartbeat_at="2024-01-01T00:00:00.000000Z", pid=99999))


# === Exception-safe release ===

def test_metadata_delete_failure_does_not_prevent_release(monkeypatch):
    from mcps.common import project_lock as _pl
    r = acquire("meta_fail", owner="T", ttl_s=10)
    orig_del = _pl._del_meta

    def failing_del(path):
        raise PermissionError("simulated failure")

    monkeypatch.setattr(_pl, "_del_meta", failing_del)
    release(r.lease)
    monkeypatch.setattr(_pl, "_del_meta", orig_del)

    r2 = acquire("meta_fail", owner="R", ttl_s=10)
    assert r2.status == "acquired"
    release(r2.lease)
    with _lock:
        assert r.lease.lease_id not in _active


# === Thread safety ===

def test_two_threads_same_owner_one_acquires():
    results = []

    def try_acquire():
        r = acquire("thread_test", owner="SAME_THREADS", ttl_s=10, wait_s=0)
        results.append(r.status)
        if r.status == "acquired":
            time.sleep(0.15)
            release(r.lease)

    t1 = threading.Thread(target=try_acquire)
    t2 = threading.Thread(target=try_acquire)
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert "acquired" in results
    assert "busy" in results
    assert results.count("acquired") == 1


# === set_lock_dir rejection with active leases ===

def test_set_lock_dir_rejected_with_active_lease():
    r = acquire("active_dir", owner="T", ttl_s=10)
    with pytest.raises(RuntimeError, match="active leases"):
        set_lock_dir(str(Path(__file__).parent / "other_locks"))
    release(r.lease)


# === set_lock_dir atomic (concurrent set + acquire) ===

def test_set_lock_dir_concurrent_safety():
    """set_lock_dir check+assign in single critical section — no TOCTOU window."""

    # Acquire first so set_lock_dir is rejected
    r = acquire("safe_proj", owner="T", ttl_s=10)

    result = []
    done = threading.Event()

    def changer():
        try:
            set_lock_dir(str(Path(__file__).parent / "safe_locks"))
            result.append("set_ok")
        except RuntimeError as e:
            result.append(f"rejected")
        done.set()

    t = threading.Thread(target=changer)
    t.start()
    assert done.wait(timeout=5), "set_lock_dir thread hung"
    assert not any("set_ok" in r for r in result), \
        f"set_lock_dir must be rejected with active lease: {result}"

    # Release → retry should succeed
    release(r.lease)
    result2 = []
    done2 = threading.Event()

    def changer2():
        try:
            set_lock_dir(str(Path(__file__).parent / "safe_locks2"))
            result2.append("set_ok")
        except RuntimeError as e:
            result2.append(f"rejected")
        done2.set()

    t2 = threading.Thread(target=changer2)
    t2.start()
    assert done2.wait(timeout=5)
    assert any("set_ok" in r for r in result2), \
        f"set_lock_dir should succeed with no active lease: {result2}"


# === wait_s ===

def test_wait_s_zero_busy():
    r1 = acquire("w0", owner="H", ttl_s=10)
    assert acquire("w0", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r1.lease)


def test_wait_s_timeout():
    r1 = acquire("wt", owner="H", ttl_s=10)
    assert acquire("wt", owner="C", ttl_s=5, wait_s=0.3).status == "timeout"
    release(r1.lease)


# === Live TTL does not break lock ===

def test_live_ttl_not_broken():
    r1 = acquire("live_ttl", owner="H", ttl_s=1)
    time.sleep(1.5)
    assert acquire("live_ttl", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r1.lease)


# === Metadata only after OS lock acquired ===

def test_stale_pid_not_deleted_while_os_lock_held():
    r1 = acquire("stale", owner="LIVE", ttl_s=10)
    import mcps.common.project_lock as _pl
    from mcps.common.project_lock import _read_meta
    lpath = _pl._lock_file_path(_pl._canonical_hash(r1.lease.lock_key))
    mpath = lpath + ".meta"
    meta = _read_meta(mpath)
    assert meta is not None
    meta["pid"] = 99999
    _pl._write_meta(mpath, Lease(
        lease_id=meta["lease_id"], owner_session_id=meta["owner_session_id"],
        lock_key=meta["lock_key"], scope=meta["scope"], mode=meta["mode"],
        ttl_s=meta["ttl_s"], acquired_at=meta["acquired_at"],
        heartbeat_at=meta["heartbeat_at"], pid=99999))
    r2 = acquire("stale", owner="C", ttl_s=5, wait_s=0)
    assert r2.status == "busy"
    assert _read_meta(mpath) is not None
    release(r1.lease)


def test_metadata_reclaimed_after_lock_acquired(tmp_path):
    d = str(tmp_path / "reclaim"); os.makedirs(d)
    marker = str(tmp_path / "child_done")
    child = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys
sys.path.insert(0, r'{PROJECT_ROOT}')
from mcps.common.project_lock import acquire, set_lock_dir
set_lock_dir(r'{d}')
r = acquire('reclaim_k', owner='CRASHER', ttl_s=1)
with open(r'{marker}', 'w') as mf:
    json.dump({{'pid': os.getpid(), 'leased': True}}, mf)
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(marker) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert os.path.exists(marker), "Child did not create marker"
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, f"Child: {err}"
        time.sleep(0.2)
        set_lock_dir(d)
        r = acquire("reclaim_k", owner="RECLAIMER", ttl_s=10)
        assert r.status == "acquired"
        release(r.lease)
    finally:
        child.kill(); child.wait()


# === Equivalent paths => same key ===

def test_equivalent_paths():
    b = os.path.join(os.getcwd(), "eq_proj")
    r1 = acquire(b, owner="T1", ttl_s=10)
    r2 = acquire("eq_proj", owner="T2", ttl_s=5, wait_s=0)
    assert r1.status == "acquired"; assert r2.status == "busy"
    release(r1.lease)


def test_different_cwd_same_absolute_path(tmp_path):
    d = str(tmp_path / "cwd_locks")
    proj = str(tmp_path / "the_proj")
    os.makedirs(proj, exist_ok=True); os.makedirs(d)
    set_lock_dir(d)  # parent must use same lock dir as child
    r1 = acquire(proj, owner="P1", ttl_s=10)

    child = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys
sys.path.insert(0, r'{PROJECT_ROOT}')
from mcps.common.project_lock import acquire, set_lock_dir
set_lock_dir(r'{d}')
os.chdir(r'{tmp_path}')
r = acquire('the_proj', owner='P2', ttl_s=5, wait_s=0)
print(json.dumps({{'status': r.status}}), flush=True)
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, f"err={err}"
        assert json.loads(out.strip())["status"] == "busy"
    finally:
        child.kill(); child.wait()
    release(r1.lease)


# === Project and JTAG keys never collide ===

def test_project_jtag_independent():
    import mcps.common.jtag_lock as _jl
    rp = acquire("pj_proj", owner="P", ttl_s=10)
    rj = _jl.acquire("localhost:3121", "pj_cable", owner="J", ttl_s=10)
    assert rp.status == rj.status == "acquired"
    kp = _project_canonical_key("pj_proj")
    from mcps.common.project_lock import _jtag_canonical_key
    kj = _jtag_canonical_key("localhost:3121", "pj_cable")
    assert kp.startswith(_PROJECT_PREFIX); assert kj.startswith("jtag:")
    assert kp != kj
    release(rp.lease); _jl.release(rj.lease)


# === Cross-process exclusive ===

def test_cross_process_exclusive(tmp_path):
    d = str(tmp_path / "xp_excl"); os.makedirs(d)
    ready = str(tmp_path / "xp_ready")
    child = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys, time
sys.path.insert(0, r'{PROJECT_ROOT}')
from mcps.common.project_lock import acquire, release, set_lock_dir
set_lock_dir(r'{d}')
r = acquire('xp_key', owner='HOLDER', ttl_s=10)
with open(r'{ready}', 'w') as rf:
    json.dump({{'status': r.status}}, rf)
time.sleep(5)
release(r.lease)
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(ready) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert os.path.exists(ready)
        set_lock_dir(d)
        r = acquire("xp_key", owner="CONTENDER", ttl_s=5, wait_s=0)
        assert r.status == "busy"
    finally:
        child.kill(); child.wait(timeout=5)


# === Windows ctypes signatures ===

def test_ctypes_signatures():
    import ctypes
    import mcps.common.project_lock as _pl
    assert _pl._k32.CreateFileW.restype is ctypes.wintypes.HANDLE
    assert _pl._k32.LockFileEx.restype is ctypes.wintypes.BOOL
    assert _pl._k32.UnlockFileEx.restype is ctypes.wintypes.BOOL
    assert _pl._k32.CloseHandle.restype is ctypes.wintypes.BOOL
