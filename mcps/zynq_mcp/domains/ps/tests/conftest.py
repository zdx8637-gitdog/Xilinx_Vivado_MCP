"""conftest.py — shared fixtures for domains/ps tests.

Defines FakeXsdbBridge, the shared test double that B06 Agent C and
Agent D both use (B06_agent_C_arm_target.md §5.1 / B06_agent_D_arm_debug.md
§5.2). It implements the XsdbBridge interface from the B06 Master §3.1 but
never launches a real process.

Keep this fake generic (canned string responses keyed by Tcl-substring) so
all four PS domain modules (jtag_target / target_control / memory_access /
target_recovery / debug_session) can reuse it.
"""

import logging
import re
import shutil
import socket

import pytest
import pytest_asyncio

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridgeError


# ── preset outputs (B06_agent_D_arm_debug.md §5.2) ─────────────────────────────
FAKE_TARGETS_OUTPUT = """
  1  ARM Cortex-A9 #0  (DAP)
  2  xc7z020  (FPGA)
"""

FAKE_BACKTRACE_OUTPUT = """
  #0  main () at main.c:42
  #1  _start () at crt0.S:15
"""

FAKE_REGISTER_OUTPUT = """
  r0: 0x00000000
  r1: 0x00100000
  pc: 0x00100040
"""


class FakeXsdbBridge:
    """Test double for XsdbBridge (no real xsdb process).

    Pre-programmed responses for Tcl commands. Supports:
    - start/stop lifecycle
    - eval with canned success responses (set_response) and canned error
      envelopes (set_error)
    - fail_eval = True simulates a dead bridge by raising XsdbBridgeError
    - _eval_history records every command sent, for call-sequence asserts
    """

    def __init__(self):
        self._success_responses: dict[str, str] = {}
        self._response_fns: dict[str, callable] = {}
        self._error_responses: dict[str, tuple[str, str]] = {}
        self._started = False
        self._hw_connected = False
        self._eval_history: list[str] = []
        self.fail_eval = False  # when True, eval raises XsdbBridgeError

    async def start(self, hw_server_url: str = "localhost:3121") -> None:
        # Mirrors the real bridge (adapters/xsct): start() launches the
        # process; the connection is established only when a non-empty url
        # is given. start("") yields a launched-but-unconnected bridge.
        self._started = True
        if hw_server_url:
            self._hw_connected = True

    async def stop(self) -> None:
        self._started = False
        self._hw_connected = False

    async def eval(self, tcl: str, timeout_s: float = 30.0) -> dict:
        self._eval_history.append(tcl)
        # Track the connection at the shell level: a `connect -url` command
        # connects, `disconnect` disconnects (mirrors real xsdb behavior).
        if re.search(r"connect\s+-url", tcl):
            self._hw_connected = True
        if tcl.strip() == "disconnect":
            self._hw_connected = False
        if self.fail_eval:
            raise XsdbBridgeError("fake bridge unavailable (simulated crash)")
        # Responses are matched by the LONGEST registered pattern that is a
        # substring of the command, so "targets -target-properties" wins over
        # "targets" for a target-properties command regardless of registration
        # order. Non-overlapping patterns are unaffected.
        err = self._longest_match(tcl, self._error_responses)
        if err is not None:
            message, code = err
            return {"status": "error", "error": {
                "code": code, "message": message,
                "details": {"reason_code": "FAKE_ERROR"}}}
        fn = self._longest_match(tcl, self._response_fns)
        if fn is not None:
            return {"status": "success", "data": fn(tcl)}
        response = self._longest_match(tcl, self._success_responses)
        if response is not None:
            return {"status": "success", "data": response}
        return {"status": "success", "data": tcl}

    @staticmethod
    def _longest_match(tcl: str, table: dict):
        """Return the value whose pattern is the longest substring of tcl."""
        best_pattern = None
        for pattern in table:
            if pattern in tcl and (best_pattern is None
                                   or len(pattern) > len(best_pattern)):
                best_pattern = pattern
        return table[best_pattern] if best_pattern is not None else None

    def set_response(self, pattern: str, output: str) -> None:
        """Program a canned success response for commands matching pattern."""
        self._success_responses[pattern] = output

    def set_error(self, pattern: str, message: str,
                  code: str = "XSDM_EVAL_ERROR") -> None:
        """Program a canned error envelope for commands matching pattern."""
        self._error_responses[pattern] = (message, code)

    def set_response_fn(self, pattern: str, fn) -> None:
        """Program a dynamic response: fn(tcl) -> str for matching commands."""
        self._response_fns[pattern] = fn

    def set_started(self, value: bool) -> None:
        """Set the launched state directly (test setup, no eval involved)."""
        self._started = bool(value)
        if not value:
            self._hw_connected = False

    def set_connected(self, value: bool) -> None:
        """Set the connection state directly (test setup, no eval involved)."""
        self._hw_connected = bool(value)

    @property
    def pid(self) -> int | None:
        return 12345 if self._started else None

    @property
    def ready(self) -> bool:
        return self._started

    @property
    def hw_connected(self) -> bool:
        return self._hw_connected


@pytest.fixture
def fake_bridge() -> FakeXsdbBridge:
    return FakeXsdbBridge()


@pytest.fixture
def connected_bridge(fake_bridge: FakeXsdbBridge) -> FakeXsdbBridge:
    """Started + connected + ARM DAP selected (setup only, no domain calls).

    The `targets` listing marks the ARM DAP with '*' (selected); the
    target-properties response reports State: Halted so get_target_status
    returns a halted target by default. The fake's longest-match rules
    make "targets -target-properties" win over "targets" for a
    target-properties command regardless of registration order.
    """
    fake_bridge.set_started(True)
    fake_bridge.set_connected(True)
    fake_bridge.set_response(
        "targets -target-properties",
        "1   ARM Cortex-A9 #0  (DAP)\n"
        "    State: Halted\n"
        "    PC: 0x00100000")
    fake_bridge.set_response(
        "targets",
        "* 1  ARM Cortex-A9 #0  (DAP)\n"
        "  2  xc7z020  (FPGA)")
    return fake_bridge


def _hw_server_up(host: str = "localhost", port: int = 3121) -> bool:
    """Probe for a running hw_server before launching a real xsdb."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def live_bridge():
    """Real XsdbBridge for host_live tests; skips when the chain is
    unavailable (no xsdb on PATH / no hw_server / bridge cannot connect).

    These tests require real xsdb + a running hw_server + a powered board
    on the JTAG chain. Missing prerequisites skip with a reason.
    """
    if not shutil.which("xsdb"):
        pytest.skip("xsdb not on PATH (host_live)")
    if not _hw_server_up():
        pytest.skip("no hw_server listening on localhost:3121 (host_live)")
    try:
        from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
    except ImportError as e:
        pytest.skip(f"xsdb_bridge import failed: {e}")
    bridge = XsdbBridge()
    try:
        await bridge.start("localhost:3121")
    except XsdbBridgeError as e:
        await bridge.stop()
        pytest.skip(f"cannot start/connect xsdb bridge: {e}")
    yield bridge
    try:
        await bridge.stop()
    except Exception as e:  # pragma: no cover - best-effort teardown
        logging.getLogger("zynq_mcp.ps.tests").warning(
            "live bridge stop failed: %s", e)
