"""T-B02-009: JTAG Lock — URL+cable canonical key, cross-process, crash recovery."""

import json, os, subprocess, sys, time
from pathlib import Path

import pytest
from mcps.common.jtag_lock import acquire, acquire_read, release, heartbeat, set_lock_dir
from mcps.common.project_lock import _jtag_canonical_key, _JTAG_PREFIX

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
assert Path(PROJECT_ROOT).name == "fpgaproject"


@pytest.fixture(autouse=True)
def _lock_dir(tmp_path):
    import mcps.common.project_lock as _pl
    d = str(tmp_path / "jtag_locks")
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

def test_empty_url():
    with pytest.raises(ValueError, match="non-empty"):
        acquire(" ", "CABLE", owner="T")


def test_empty_serial():
    with pytest.raises(ValueError, match="non-empty"):
        acquire("localhost:3121", "  ", owner="T")


# === Basic lifecycle ===

def test_acquire_release():
    r = acquire("localhost:3121", "D1234567", owner="PS", ttl_s=10)
    assert r.status == "acquired"; assert r.lease.scope == "jtag"
    release(r.lease)


def test_reacquire():
    r = acquire("localhost:3121", "D9", owner="T1", ttl_s=10); release(r.lease)
    r2 = acquire("localhost:3121", "D9", owner="T2", ttl_s=10)
    assert r2.status == "acquired"; release(r2.lease)


# === Same cable blocked ===

def test_same_cable_busy():
    r1 = acquire("localhost:3121", "A", owner="PL", ttl_s=10)
    assert acquire("localhost:3121", "A", owner="PS", ttl_s=5, wait_s=0).status == "busy"
    release(r1.lease)


# === Different cables parallel ===

def test_different_cables():
    r1 = acquire("localhost:3121", "X", owner="A", ttl_s=10)
    r2 = acquire("localhost:3121", "Y", owner="B", ttl_s=10)
    assert r1.status == r2.status == "acquired"
    release(r1.lease); release(r2.lease)


# === URL normalization ===

def test_trailing_slash():
    r1 = acquire("localhost:3121/", "Z", owner="T", ttl_s=10)
    assert acquire("localhost:3121", "Z", owner="T2", ttl_s=5, wait_s=0).status == "busy"
    release(r1.lease)


# === Length-delimited key prevents colon collision ===

def test_url_with_colon_ipv6():
    r1 = acquire("[::1]:3121", "S", owner="T1", ttl_s=10)
    r2 = acquire("localhost:3121", "S", owner="T2", ttl_s=10)
    assert r1.status == r2.status == "acquired"
    release(r1.lease); release(r2.lease)


def test_length_delimited_prevents_ambiguity():
    k1 = _jtag_canonical_key("a", "b:c")
    k2 = _jtag_canonical_key("a:b", "c")
    assert k1 != k2
    assert k1.startswith(_JTAG_PREFIX); assert k2.startswith(_JTAG_PREFIX)


# === Different cwd, same jtag device still blocked ===

def test_different_cwd_same_device(tmp_path):
    d = str(tmp_path / "jtag_cwd"); os.makedirs(d)
    set_lock_dir(d)
    r1 = acquire("localhost:3121", "CWD", owner="P1", ttl_s=10)

    child = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, sys
sys.path.insert(0, r'{PROJECT_ROOT}')
from mcps.common.jtag_lock import acquire, set_lock_dir
import os
os.chdir(r'{tmp_path}')
set_lock_dir(r'{d}')
r = acquire('localhost:3121', 'CWD', owner='P2', ttl_s=5, wait_s=0)
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


# === Crash recovery ===

def test_jtag_crash_recovery(tmp_path):
    d = str(tmp_path / "jtag_crash"); os.makedirs(d)
    ready = str(tmp_path / "jtag_crash_ready")
    child = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys
sys.path.insert(0, r'{PROJECT_ROOT}')
from mcps.common.jtag_lock import acquire, set_lock_dir
set_lock_dir(r'{d}')
r = acquire('localhost:3121', 'CRASH', owner='CRASHER', ttl_s=1)
with open(r'{ready}', 'w') as rf:
    json.dump({{'status': r.status, 'pid': os.getpid()}}, rf)
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(ready) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert os.path.exists(ready), "Child did not create marker"
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, f"err={err}"
        time.sleep(0.2)
        set_lock_dir(d)
        r = acquire("localhost:3121", "CRASH", owner="RECLAIMER", ttl_s=10)
        assert r.status == "acquired"
        release(r.lease)
    finally:
        child.kill(); child.wait()


# === Heartbeat ===

def test_heartbeat():
    r = acquire("localhost:3121", "HB", owner="T", ttl_s=10)
    old = r.lease.heartbeat_at
    new = heartbeat(r.lease)
    assert new.heartbeat_at > old
    release(r.lease)


def test_heartbeat_read_raises():
    r = acquire_read("localhost:3121", "HBR", owner="T")
    with pytest.raises(RuntimeError, match="read lease"):
        heartbeat(r.lease)
    release(r.lease)


# === Forged release rejected ===

def test_wrong_owner_rejected():
    from mcps.common.project_lock import Lease
    r = acquire("localhost:3121", "WO", owner="REAL", ttl_s=10)
    fake = Lease(
        lease_id=r.lease.lease_id, owner_session_id="FAKE",
        lock_key=r.lease.lock_key, scope=r.lease.scope, mode=r.lease.mode,
        ttl_s=r.lease.ttl_s, acquired_at=r.lease.acquired_at,
        heartbeat_at=r.lease.heartbeat_at, pid=r.lease.pid)
    with pytest.raises(RuntimeError, match="owner_session_id"):
        release(fake)
    assert acquire("localhost:3121", "WO", owner="C", ttl_s=5, wait_s=0).status == "busy"
    release(r.lease)


# === Cross-process jtag busy ===

def test_jtag_cross_process_busy(tmp_path):
    d = str(tmp_path / "jtag_busy"); os.makedirs(d)
    ready = str(tmp_path / "jtag_busy_ready")
    child = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys, time
sys.path.insert(0, r'{PROJECT_ROOT}')
from mcps.common.jtag_lock import acquire, release, set_lock_dir
set_lock_dir(r'{d}')
r = acquire('localhost:3121', 'BUSY', owner='HOLDER', ttl_s=10)
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
        assert os.path.exists(ready), "Child did not create marker"
        set_lock_dir(d)
        r = acquire("localhost:3121", "BUSY", owner="C", ttl_s=5, wait_s=0)
        assert r.status == "busy"
    finally:
        child.kill(); child.wait(timeout=5)


# === Real project/jtag independence ===

def test_project_and_jtag_dual_acquire():
    import mcps.common.project_lock as _pl
    rp = _pl.acquire("dual_proj", owner="P", ttl_s=10)
    rj = acquire("localhost:3121", "DUAL", owner="J", ttl_s=10)
    assert rp.status == rj.status == "acquired"
    assert rp.lease.lock_key != rj.lease.lock_key
    _pl.release(rp.lease); release(rj.lease)
