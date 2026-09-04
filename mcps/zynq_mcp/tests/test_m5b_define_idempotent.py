"""test_m5b_define_idempotent.py — B13-M5b: compile_app define re-application.

Real-XSCT-verified behavior (2026-09-03 probe): ``app config -add
define-compiler-symbols <sym>`` fails with "Defined symbols (-D) already
contains the item <sym>" when the symbol is already persisted in the
workspace. The P2 real-board incident: ps_compile failed exactly on this
re-application. The fix treats that specific error as an idempotent success;
any other define-add error still fails the build.
"""
import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from mcps.zynq_mcp.domains.ps import ps_bsp


class _DefineBridge:
    def __init__(self):
        self.ready = True
        self.workspace = ""
        self.add_calls = 0
        self.build_calls = 0

    async def start(self, workspace=""):
        self.ready = True
        self.workspace = workspace

    async def stop(self):
        self.ready = False

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        if "define-compiler-symbols" in tcl:
            self.add_calls += 1
            if self.add_calls >= 2:
                return {"status": "success", "data":
                        "__ERROR__:XSDM_EVAL_ERROR:Defined symbols (-D) "
                        "already contains the item B13_PL_CHAIN"}
            return {"status": "success", "data": ""}
        if "app build" in tcl:
            self.build_calls += 1
            return {"status": "success", "data": ""}
        if "setws" in tcl:
            self.workspace = tcl.split()[-1].strip()
            return {"status": "success", "data": ""}
        return {"status": "success", "data": ""}


def _dummy_elf(path):
    h = bytearray(52)
    h[:4] = b"\x7fELF"
    h[4] = 1
    h[5] = 1
    h[16:18] = (2).to_bytes(2, "little")
    h[18:20] = (40).to_bytes(2, "little")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(h))


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "app" / "src").mkdir(parents=True)
    _dummy_elf(w / "app" / "Debug" / "app.elf")
    ps_bsp._WS_DEFINES.pop(str(w), None)
    yield str(w)
    ps_bsp._WS_DEFINES.pop(str(w), None)


class TestDefineIdempotent:
    @pytest.mark.asyncio
    async def test_second_build_with_same_defines_succeeds(self, ws):
        ps_bsp._WS_DEFINES[ws] = "B13_PL_CHAIN"
        b1 = _DefineBridge()
        b1.workspace = ws
        r1 = await ps_bsp.compile_app(b1, "app")
        assert r1.get("status") == "success", r1
        assert b1.add_calls == 1
        # Second build: the define is already persisted → the -add errors with
        # "already contains the item" → must be tolerated (idempotent).
        b2 = _DefineBridge()
        b2.workspace = ws
        r2 = await ps_bsp.compile_app(b2, "app")
        assert r2.get("status") == "success", r2
        assert b2.add_calls == 1  # the tolerant path still attempted once

    @pytest.mark.asyncio
    async def test_other_define_error_still_fails(self, ws):
        ps_bsp._WS_DEFINES[ws] = "B13_PL_CHAIN"

        class _OtherErrBridge(_DefineBridge):
            async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
                if "define-compiler-symbols" in tcl:
                    return {"status": "success", "data":
                            "__ERROR__:XSDM_EVAL_ERROR:bad option -add"}
                return {"status": "success", "data": ""}

        bridge = _OtherErrBridge()
        bridge.workspace = ws
        r = await ps_bsp.compile_app(bridge, "app")
        assert r.get("status") == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
