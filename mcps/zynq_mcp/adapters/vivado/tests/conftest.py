"""conftest.py — shared fixtures for adapters/vivado tests (B08).

The fake vivado shell is a real Python subprocess (not a mock): it reads lines
from stdin and echoes results back, so the VivadoTclBridge exercises the full
create_subprocess_exec + stdin/stdout pipe + sentinel-marker path without a
real Vivado install. It mimics real ``vivado -mode tcl`` behavior:
  - prints a banner at startup;
  - prefixes every ``puts`` result with the ``% `` prompt (mirrors Vivado);
  - re-prints the banner on ``__BANNER__`` (mirrors Vivado's per-command
    banner reprint through a pipe).

Special commands:
  - __HANG__    sleep 60s (drives the eval-timeout path)
  - __ERROR__   write "ERROR: simulated vivado tcl error" to stdout
  - __STDERR__  write "noise on stderr" to stderr (must NOT fail vivado)
  - __BANNER__  re-print the startup banner (banner-filter path)
  - __DUMP__    echo every command received so far (call-sequence asserts)
Any other non-puts line is recorded and echoed as "OK <line>" (a command
returning a result).
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
out("****** Vivado v2023.1 (64-bit) ****")
out("**** SW Build 4028589 (win64) ****")
out("** Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.")

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
        out("ERROR: simulated vivado tcl error")
        continue
    if line == "__STDERR__":
        err("noise on stderr")
        continue
    if line == "__BANNER__":
        out("****** Vivado v2023.1 (64-bit) ****")
        out("**** SW Build 4028589 (win64)")
        out("OK __BANNER__")
        continue
    if line == "__DUMP__":
        out("DUMP_BEGIN")
        for r in records:
            out("RECORDED " + r)
        out("DUMP_END")
        continue
    if line.startswith("puts "):
        # Vivado prefixes puts results with the `% ` prompt.
        out("% " + line[5:])
        continue
    records.append(line)
    out("OK " + line)
'''


@pytest.fixture
def fake_shell_path(tmp_path):
    """Write the fake vivado shell to a temp .py file and return its path."""
    p = tmp_path / "fake_vivado_shell.py"
    p.write_text(FAKE_SHELL_SRC, encoding="utf-8")
    return str(p)


@pytest.fixture
def fake_shell_cmd(fake_shell_path):
    """Launch command: [python, fake_shell.py]."""
    return [sys.executable, fake_shell_path]


@pytest.fixture
def fake_vivado(fake_shell_cmd):
    """A VivadoTclBridge whose subprocess is the fake vivado shell."""
    from mcps.zynq_mcp.adapters.vivado.vivado_bridge import VivadoTclBridge
    return VivadoTclBridge(vivado_path=fake_shell_cmd)
