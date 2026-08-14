"""Isolation tests for env_probe.py — process and filesystem safety."""

import os, sys, hashlib, time, pytest

# == Test A: caller cwd never modified ==

def test_vivado_probe_cwd_isolation(tmp_path, monkeypatch):
    """Pre-existing vivado.jou/log SHA256s unchanged.
    _run_command cwd is a TemporaryDirectory, not the caller's dir.
    Temp directory is cleaned up after call. No new artifacts in caller dir."""

    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    jou_path = caller_dir / "vivado.jou"
    log_path = caller_dir / "vivado.log"
    jou_path.write_text("pre-existing jou\n")
    log_path.write_text("pre-existing log\n")
    jou_sha = hashlib.sha256(jou_path.read_bytes()).hexdigest()
    log_sha = hashlib.sha256(log_path.read_bytes()).hexdigest()

    monkeypatch.chdir(str(caller_dir))

    captured = []

    def fake_run_command(args, runner=None, timeout=60, cwd=None):
        captured.append({"args": args, "cwd": cwd, "timeout": timeout})
        return "SW Build 0\n__VERSION=2023.1\n", "", 0

    monkeypatch.setattr("mcps.common.env_probe._run_command", fake_run_command)

    from mcps.common.env_probe import _verify_vivado
    sup, ver, _, _, _, _ = _verify_vivado("F:/fake/vivado.bat")
    assert sup is True
    assert ver == "2023.1"

    # cwd assertions
    assert len(captured) == 1
    cwd = captured[0]["cwd"]
    assert cwd is not None, "_run_command not called with cwd parameter"
    assert cwd != str(caller_dir), f"cwd ({cwd}) must not be caller_dir"
    # cwd must be from TemporaryDirectory
    assert "vivado_probe" in cwd or "tmp" in cwd.lower(), \
        f"Expected tempdir cwd, got {cwd}"

    # The temp dir must have existed during the call and be gone now
    assert not os.path.exists(cwd), (
        f"TemporaryDirectory {cwd} should be cleaned up after _verify_vivado")

    # SHA256s unchanged
    assert hashlib.sha256(jou_path.read_bytes()).hexdigest() == jou_sha
    assert hashlib.sha256(log_path.read_bytes()).hexdigest() == log_sha

    # No new files in caller dir
    caller_files = set(p.name for p in caller_dir.iterdir())
    assert caller_files == {"vivado.jou", "vivado.log"}, \
        f"Unexpected files: {caller_files}"

    # -nolog -nojournal -notrace in args
    flat_args = " ".join(captured[0]["args"])
    for flag in ("-nolog", "-nojournal", "-notrace"):
        assert flag in flat_args


# == Test B: timeout process tree cleanup ==

def _is_pid_alive(pid):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x0400, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def test_run_command_timeout_kills_process_tree(tmp_path):
    """_run_command timeout kills process tree. Helper writes PIDs to file."""
    if os.name != "nt":
        pytest.skip("Process tree test is Windows-only")

    pids_file = tmp_path / "pids.txt"
    helper_py = tmp_path / "helper.py"
    helper_code = (
        "import subprocess, sys, os\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "    stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
        "with open(r'" + pids_file.as_posix() + "', 'w') as f:\n"
        "    f.write(str(os.getpid()) + '\\n')\n"
        "    f.write(str(child.pid) + '\\n')\n"
        "try:\n"
        "    child.wait(timeout=150)\n"
        "except subprocess.TimeoutExpired:\n"
        "    pass\n"
    )
    helper_py.write_text(helper_code)

    from mcps.common.env_probe import _run_command
    expected_pids = []

    try:
        args = [sys.executable, str(helper_py)]
        stdout, stderr, exit_code = _run_command(args, timeout=3,
                                                  cwd=str(tmp_path))

        assert exit_code is None, (
            f"Expected timeout exit_code=None, got {exit_code}")

        if pids_file.exists():
            expected_pids = [int(l.strip())
                             for l in pids_file.read_text().splitlines()
                             if l.strip()]

        assert len(expected_pids) >= 2, (
            f"Expected >=2 PIDs, got {expected_pids}")

        time.sleep(2)

        for pid in expected_pids:
            assert not _is_pid_alive(pid), \
                f"PID {pid} should be dead after timeout"
    finally:
        for pid in expected_pids:
            try:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
            except (OSError, PermissionError):
                pass


# == Test C: normal success path ==

def test_run_command_success_exits_cleanly():
    """_run_command on success returns correct exit code and output."""
    from mcps.common.env_probe import _run_command
    stdout, stderr, exit_code = _run_command(
        ["cmd.exe", "/d", "/c", "echo hello"], timeout=10)
    assert exit_code == 0
    assert "hello" in stdout
