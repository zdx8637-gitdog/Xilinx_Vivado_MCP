"""test_uart_diagnostics.py — unit tests for the B01 §5 Phase 7 UART
diagnosis cascade (domains/ps/uart_diagnostics.py) plus its registration
as the `ps_diagnose_uart_clock` MCP tool.

No real XSDB / hw_server / JTAG required: diagnose_uart_clock is exercised
with a FakeBridge that records eval calls and returns canned register /
memory values (TEST_HELPER evidence level), matching the pattern used by
test_ps_bsp_domain.py. The real end-to-end flow is covered by the
host_live tests in test_b06_ps_public.py.
"""
from __future__ import annotations

import pytest

from mcps.zynq_mcp.domains.ps import uart_diagnostics
from mcps.zynq_mcp.domains.ps.uart_diagnostics import diagnose_uart_clock

pytestmark = pytest.mark.asyncio(loop_scope="function")

# Default canned values (Zynq-7020 typical):
#   SLCR UART_CLK_CTRL @ 0xF8000154 = 0x00000A01  -> DIVISOR0 = 10  -> /11
#   UART1 BAUDGEN    @ 0xE0001018   = 0x00000031  -> CD = 49
#   UART1 BAUDDIV    @ 0xE0001034   = 0x00000010  -> BDIV = 16
#   UART_REF = 1000 MHz / 11 = 90,909,090 Hz
#   Baud     = 90,909,090 / (49 * 16) = 115,955  (~0.66 % above 115200)
_SLCR = 0xF8000154
_BAUDGEN = 0xE0001018
_BAUDDIV = 0xE0001034
_DEFAULT_MEM = {
    _SLCR: "0x00000A01",
    _BAUDGEN: "0x00000031",
    _BAUDDIV: "0x00000010",
}

_EVAL_ERR = {"status": "error", "error": {
    "code": "XSDM_EVAL_ERROR", "message": "boom",
    "details": {"reason_code": "XSDM_TCL_ERROR"}}}


class FakeBridge:
    """In-memory XsdbBridge double.

    hw_connected=True and a `targets` listing that marks a target selected,
    so require_connected()/require_target_selected() pass. eval() dispatches
    on the Tcl command and returns canned register/memory values; every eval
    is recorded in `calls`. `fail` lists register names or addresses whose
    eval must return a bridge error (fail-closed path).
    """

    def __init__(self, regs=None, mem=None, fail=None):
        self.hw_connected = True
        self.regs = dict(regs or {"pc": "0xFFFFFF28", "cpsr": "0x60000013"})
        self.mem = dict(mem or _DEFAULT_MEM)
        self.fail = set(fail or [])
        self.calls = []

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        self.calls.append(tcl)
        cmd = tcl.strip()
        if cmd == "targets":
            return {"status": "success",
                    "data": "* 2  ARM Cortex-A9 MPCore #0"}
        if cmd.startswith("rrd "):
            reg = cmd.split(None, 1)[1].strip()
            if reg in self.fail:
                return dict(_EVAL_ERR)
            return {"status": "success",
                    "data": f"{reg}: {self.regs.get(reg, '0x00000000')}"}
        if cmd.startswith("mrd "):
            parts = cmd.split()
            if len(parts) >= 2:
                addr = int(parts[1], 16)
                if addr in self.fail:
                    return dict(_EVAL_ERR)
                return {"status": "success",
                        "data": f"{parts[1]}: {self.mem.get(addr, '0x00000000')}"}
        return {"status": "success", "data": ""}


# ── success paths ─────────────────────────────────────────────────────────────

class TestBaudMatch:

    async def test_baud_match_reports_baud_ok(self):
        """Default values compute 115,955 baud vs expected 115,200 (0.66 %
        error) -> baud_match=True, diagnosis='baud_ok'."""
        bridge = FakeBridge()
        r = await diagnose_uart_clock(bridge)
        assert r["status"] == "success", r
        d = r["data"]
        assert d["computed_baud"] == 115955
        assert d["expected_baud"] == 115200
        assert d["baud_match"] is True
        assert d["diagnosis"] == "baud_ok"
        assert d["baud_error_pct"] < 1.0
        assert d["baud_error_pct"] > 0.0

    async def test_baud_mismatch_reports_baud_error(self):
        """CD=30 gives 189,393 baud (64 % off) -> baud_match=False."""
        bridge = FakeBridge(mem={**dict(_DEFAULT_MEM), _BAUDGEN: "0x0000001E"})
        r = await diagnose_uart_clock(bridge)
        assert r["status"] == "success", r
        d = r["data"]
        assert d["baud_match"] is False
        assert d["diagnosis"] == "baud_mismatch"
        assert d["baud_error_pct"] > 0
        assert d["computed_baud"] == 189393

    async def test_all_reads_succeed_and_data_shape(self):
        """Every read succeeds -> computed_baud is non-zero and every
        requested field is present in the success data."""
        bridge = FakeBridge()
        r = await diagnose_uart_clock(bridge)
        assert r["status"] == "success", r
        d = r["data"]
        assert d["computed_baud"] > 0
        for key in ("pc", "cpsr", "slcr_uart_clk_ctrl", "uart1_baudgen",
                    "uart1_bauddiv", "computed_baud", "expected_baud",
                    "baud_match", "baud_error_pct", "diagnosis"):
            assert key in d, f"missing {key}"
        assert d["pc"].startswith("0x")
        assert d["cpsr"].startswith("0x")
        assert d["slcr_uart_clk_ctrl"] == "0xA01"
        assert d["uart1_baudgen"] == 49
        assert d["uart1_bauddiv"] == 16
        assert d["diagnosis"] in ("baud_ok", "baud_mismatch")

    async def test_expected_baud_parameter_respected(self):
        """A custom expected_baud changes the match outcome for the same
        registers: CD=49/BDIV=16 computes 115,955, so an expected of
        116,000 is a match while 115,200 is also a match."""
        bridge = FakeBridge()
        r = await diagnose_uart_clock(bridge, expected_baud=116000)
        assert r["status"] == "success", r
        assert r["data"]["expected_baud"] == 116000
        assert r["data"]["baud_match"] is True


# ── fail-closed paths ─────────────────────────────────────────────────────────

class TestFailClosed:

    async def test_reg_read_failure_is_fail_closed(self):
        """A failed 'rrd pc' aborts the cascade with an error envelope that
        preserves REG_READ_FAILED and marks diagnosis='reg_read_failed'."""
        bridge = FakeBridge(fail={"pc"})
        r = await diagnose_uart_clock(bridge)
        assert r["status"] == "error", r
        err = r["error"]
        assert err["code"] == "JTAG_ERROR", err
        assert err["details"]["reason_code"] == "REG_READ_FAILED"
        assert err["details"]["diagnosis"] == "reg_read_failed"
        assert err["details"]["failed_read"] == "pc"
        # The cascade must stop at the first failure (no memory reads).
        assert not any(c.startswith("mrd") for c in bridge.calls)

    async def test_mem_read_failure_is_fail_closed(self):
        """A failed SLCR read aborts with MEM_READ_FAILED preserved."""
        bridge = FakeBridge(fail={_SLCR})
        r = await diagnose_uart_clock(bridge)
        assert r["status"] == "error", r
        err = r["error"]
        assert err["code"] == "JTAG_ERROR", err
        assert err["details"]["reason_code"] == "MEM_READ_FAILED"
        assert err["details"]["diagnosis"] == "reg_read_failed"
        assert err["details"]["failed_read"] == "slcr_uart_clk_ctrl"

    async def test_zero_baud_divider_is_fail_closed(self):
        """CD=0 makes the divider zero -> UART_ERROR, baud_unconfigured."""
        bridge = FakeBridge(mem={**dict(_DEFAULT_MEM), _BAUDGEN: "0x00000000"})
        r = await diagnose_uart_clock(bridge)
        assert r["status"] == "error", r
        err = r["error"]
        assert err["code"] == "UART_ERROR", err
        assert err["details"]["reason_code"] == "UART_BAUD_UNCONFIGURED"
        assert err["details"]["diagnosis"] == "baud_unconfigured"
        assert err["details"]["uart1_baudgen"] == 0

    async def test_invalid_expected_baud_rejected(self):
        """expected_baud must be a positive int -> INVALID_ARGUMENT before
        any bridge read."""
        for bad in (0, -1, True, 115200.5, "115200"):
            bridge = FakeBridge()
            r = await diagnose_uart_clock(bridge, expected_baud=bad)
            assert r["status"] == "error", f"{bad!r}: {r}"
            assert r["error"]["code"] == "INVALID_ARGUMENT", f"{bad!r}: {r}"
            assert r["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
            assert bridge.calls == [], \
                f"no eval may happen before validation for {bad!r}"


# ── FakeBridge contract: no real XSDB, exact diagnosis sequence ───────────────

class TestFakeBridgeContract:

    async def test_eval_sequence_is_the_diagnosis_cascade(self):
        """The bridge sees exactly the Phase 7 reads: PC, CPSR, then the
        three UART/SLCR registers — each read preceded by a `targets`
        listing (require_target_selected)."""
        bridge = FakeBridge()
        await diagnose_uart_clock(bridge)
        reads = [c for c in bridge.calls if not c.startswith("targets")]
        assert reads == [
            "rrd pc",
            "rrd cpsr",
            "mrd 0xF8000154 1",
            "mrd 0xE0001018 1",
            "mrd 0xE0001034 1",
        ], reads
        assert bridge.calls.count("targets") == 5, bridge.calls
        assert bridge.hw_connected is True


# ── registration: capabilities schema + dispatcher routing ───────────────────

class TestRegistration:

    async def test_tool_schema_registered(self):
        """ps_diagnose_uart_clock is advertised in list_tools with the
        optional expected_baud (integer) input."""
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        tools = {t.name: t for t in ALL_TOOLS}
        assert "ps_diagnose_uart_clock" in tools, \
            "ps_diagnose_uart_clock not in ALL_TOOLS"
        tool = tools["ps_diagnose_uart_clock"]
        schema = tool.inputSchema
        assert schema["type"] == "object"
        props = schema.get("properties", {})
        assert "expected_baud" in props, "expected_baud property missing"
        assert props["expected_baud"]["type"] == "integer"
        assert "expected_baud" not in schema.get("required", []), \
            "expected_baud is optional"

    async def test_dispatcher_routing(self):
        """_PS_TOOL_MAP routes ps_diagnose_uart_clock to
        uart_diagnostics.diagnose_uart_clock; the name is known to the
        dispatcher."""
        from mcps.zynq_mcp.dispatcher import (
            _ALL_KNOWN, _PS_TOOL_NAMES, _PS_TOOL_MAP,
        )
        assert "ps_diagnose_uart_clock" in _PS_TOOL_NAMES
        assert "ps_diagnose_uart_clock" in _ALL_KNOWN
        entry = _PS_TOOL_MAP["ps_diagnose_uart_clock"]
        assert isinstance(entry, tuple)
        assert entry[0] is uart_diagnostics
        assert entry[1] == "diagnose_uart_clock"
        assert callable(getattr(uart_diagnostics, "diagnose_uart_clock"))
