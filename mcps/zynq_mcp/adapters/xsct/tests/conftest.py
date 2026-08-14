"""conftest.py — shared fixtures for adapters/xsct tests (B06 Agent A).

The fake Tcl shell is a real Python subprocess (not a mock): it reads lines
from stdin and echoes them back, so the bridge exercises the full
create_subprocess_exec + stdin/stdout pipe + sentinel-marker path without
needing a real Xilinx tool. Special commands:
  - __HANG__   sleep 60s (drives the eval-timeout path)
  - __ERROR__  write "ERROR: simulated tcl error" to stderr
  - __STDERR__ write "noise on stderr" to stderr
  - __DUMP__   echo every command received so far (call-sequence asserts)
Any other line that is not a `puts` line is recorded and echoed as
"OK <line>" (mimics a command returning a result).
"""

import sys

import pytest

FAKE_SHELL_SRC = r'''import sys, time


def out(s):
    sys.stdout.write(s + "\n")
    sys.stdout.flush()


def err(s):
    sys.stderr.write(s + "\n")
    sys.stderr.flush()


records = []
out("fake tcl shell ready")

for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.strip():
        continue
    if line == "exit":
        break
    if line == "__HANG__":
        time.sleep(60)
        continue
    if line == "__ERROR__":
        out("ERROR: simulated tcl error")
        continue
    if line == "__STDERR__":
        err("noise on stderr")
        continue
    if line == "__DUMP__":
        out("DUMP_BEGIN")
        for r in records:
            out("RECORDED " + r)
        out("DUMP_END")
        continue
    if line.startswith("puts "):
        out(line[5:])
        continue
    records.append(line)
    out("OK " + line)
'''


@pytest.fixture
def fake_shell_path(tmp_path):
    """Write the fake Tcl shell to a temp .py file and return its path."""
    p = tmp_path / "fake_tcl_shell.py"
    p.write_text(FAKE_SHELL_SRC, encoding="utf-8")
    return str(p)


@pytest.fixture
def fake_shell_cmd(fake_shell_path):
    """Launch command: [python, fake_shell.py]."""
    return [sys.executable, fake_shell_path]


@pytest.fixture
def fake_xsdb(fake_shell_cmd):
    """An XsdbBridge whose subprocess is the fake Tcl shell."""
    from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
    return XsdbBridge(xsdb_path=fake_shell_cmd)


@pytest.fixture
def fake_xsct(fake_shell_cmd):
    """An XsctBridge whose subprocess is the fake Tcl shell."""
    from mcps.zynq_mcp.adapters.xsct.xsct_bridge import XsctBridge
    return XsctBridge(xsct_path=fake_shell_cmd)
