"""test_ps_bsp_domain.py — unit tests for ps_bsp domain functions and the
XsctBridge tolerant-stderr parse path.

No XSCT / hardware required: ps_bsp functions are exercised with a fake
bridge that records eval calls and returns canned responses (TEST_HELPER
evidence level). The real end-to-end flow is covered by the host_live
tests in test_b06_ps_bsp_public.py.
"""
from __future__ import annotations

import os
import shutil
import struct

import pytest

from mcps.zynq_mcp.adapters.xsct.xsct_bridge import (
    XsctBridge,
    _TCLERR_MARKER,
    _catch_wrap,
)
from mcps.zynq_mcp.domains.ps import ps_bsp

pytestmark = pytest.mark.asyncio(loop_scope="function")

_OK = {"status": "success", "data": ""}


class FakeXsctBridge:
    """Canned-response bridge recording eval calls (no real process)."""

    def __init__(self, results=None, ready=True, workspace="D:/ws"):
        self._results = list(results or [])
        self.ready = ready
        self.workspace = workspace
        self.calls = []

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        self.calls.append((tcl, timeout_s, tolerate_stderr))
        if self._results:
            return self._results.pop(0)
        return dict(_OK)


def _err(reason="XSDM_TCL_ERROR", message="boom"):
    return {"status": "error", "error": {
        "code": "XSDM_EVAL_ERROR", "message": message,
        "details": {"reason_code": reason}}}


def _write_elf(path, entry=0x10000000, e_machine=40, e_type=2):
    hdr = bytearray(52)
    hdr[0:4] = b"\x7fELF"
    hdr[4] = 1   # ELFCLASS32
    hdr[5] = 1   # LSB
    struct.pack_into("<HHII", hdr, 16, e_type, e_machine, 1, entry)
    with open(path, "wb") as f:
        f.write(bytes(hdr))
    return path


# ── input validation (no bridge calls) ───────────────────────────────────────

class TestInputValidation:

    async def test_import_hardware_bad_xsa_path(self):
        bridge = FakeXsctBridge()
        r = await ps_bsp.import_hardware(bridge, "   ", "D:/ws")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_XSA_PATH"
        assert r["error"]["code"] == "INVALID_ARGUMENT"
        assert bridge.calls == [], "no eval may happen before validation"

    async def test_import_hardware_xsa_not_found(self, tmp_path):
        bridge = FakeXsctBridge()
        r = await ps_bsp.import_hardware(bridge,
                                         str(tmp_path / "nope.xsa"), "D:/ws")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "XSA_NOT_FOUND"

    async def test_import_hardware_path_escape(self):
        bridge = FakeXsctBridge()
        r = await ps_bsp.import_hardware(
            bridge, "D:/../../etc/passwd.xsa", "D:/ws")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "PATH_ESCAPE"

    async def test_create_platform_invalid_name(self):
        bridge = FakeXsctBridge()
        r = await ps_bsp.create_platform(bridge, "a/b", "D:/ws")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_NAME"

    async def test_bridge_not_ready_is_fail_closed(self, tmp_path):
        xsa = tmp_path / "a.xsa"
        xsa.write_bytes(b"not a real xsa but the file must exist")
        bridge = FakeXsctBridge(ready=False)
        r = await ps_bsp.import_hardware(bridge, str(xsa), str(tmp_path))
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BRIDGE_NOT_READY"

    async def test_import_hardware_is_idempotent_when_xsa_is_in_workspace(
            self, tmp_path):
        xsa = tmp_path / "platform.xsa"
        payload = b"already published in the XSCT workspace"
        xsa.write_bytes(payload)
        bridge = FakeXsctBridge(workspace=str(tmp_path))

        r = await ps_bsp.import_hardware(
            bridge, str(xsa), str(tmp_path))

        assert r["status"] == "success"
        assert r["data"]["imported"] is True
        assert r["data"]["copied"] is False
        assert xsa.read_bytes() == payload

    async def test_create_platform_no_xsa_in_workspace(self, tmp_path):
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.create_platform(bridge, "plat", str(tmp_path))
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "XSA_NOT_FOUND"

    async def test_create_app_no_platform(self, tmp_path):
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.create_app(bridge, "app", str(tmp_path))
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "PLATFORM_NOT_FOUND"

    async def test_add_sources_not_a_list(self):
        bridge = FakeXsctBridge()
        r = await ps_bsp.add_sources(bridge, "myapp", "main.c")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_FILES"

    async def test_set_compiler_options_unknown_option(self):
        bridge = FakeXsctBridge()
        r = await ps_bsp.set_compiler_options(bridge, {"bogus": "-O2"})
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_OPTION"
        assert r["error"]["code"] == "INVALID_ARGUMENT"

    async def test_compile_app_build_failure_maps_ps_build_error(self):
        bridge = FakeXsctBridge(results=[_err()])
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        assert r["error"]["code"] == "PS_BUILD_ERROR", r
        assert bridge.calls[0][0] == "app build -name myapp"


# ── add_sources (C2 fix: explicit app_name + host copy into app/src) ─────────
# B09 black-box found the old implementation guessing the app from the
# workspace and importing via XSCT, placing files at {ws}/src instead of
# {ws}/{app}/src. The fix copies deterministically on the host; these tests
# assert the real filesystem outcome (never a hand-built envelope).

class TestAddSources:

    async def test_add_sources_invalid_app_name(self, tmp_path):
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.add_sources(bridge, "a/b", [])
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_APP_NAME"
        assert r["error"]["code"] == "INVALID_ARGUMENT"
        assert bridge.calls == [], "no eval may happen before validation"

    async def test_add_sources_app_has_no_src_dir(self, tmp_path):
        (tmp_path / "myapp").mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        main = staging / "main.c"
        main.write_text("int main(void){return 0;}\n")
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.add_sources(bridge, "myapp", [str(main)])
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "APP_NOT_FOUND"
        assert bridge.calls == []

    async def test_add_sources_copies_into_app_src_not_workspace_src(
            self, tmp_path):
        """C2 regression: file must land at {ws}/{app}/src/main.c — never
        at the B09 wrong location {ws}/src/main.c."""
        (tmp_path / "myapp" / "src").mkdir(parents=True)
        staging = tmp_path / "staging"
        staging.mkdir()
        main = staging / "main.c"
        main.write_text("int main(void){return 0;}\n")
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.add_sources(bridge, "myapp", [str(main)])
        assert r["status"] == "success", r
        placed = r["data"]["files"]
        expected = (tmp_path / "myapp" / "src" / "main.c")
        assert placed == [str(expected).replace("\\", "/")], placed
        assert expected.is_file()
        assert expected.read_text() == "int main(void){return 0;}\n"
        # The wrong B09 location must NOT exist.
        assert not (tmp_path / "src" / "main.c").exists()
        # The fix is a pure host copy — no XSCT eval is issued.
        assert bridge.calls == []

    async def test_add_sources_copies_all_files(self, tmp_path):
        """Every file is copied — not just the first (old code passed only
        files[0] to importsources)."""
        (tmp_path / "myapp" / "src").mkdir(parents=True)
        staging = tmp_path / "staging"
        staging.mkdir()
        main = staging / "main.c"
        main.write_text("int main(void){return 0;}\n")
        extra = staging / "extra.c"
        extra.write_text("int extra(void){return 42;}\n")
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.add_sources(bridge, "myapp",
                                     [str(main), str(extra)])
        assert r["status"] == "success", r
        assert (tmp_path / "myapp" / "src" / "main.c").is_file()
        assert (tmp_path / "myapp" / "src" / "extra.c").is_file()
        assert len(r["data"]["files"]) == 2
        assert bridge.calls == []

    async def test_add_sources_duplicate_basename_rejected(self, tmp_path):
        """Two inputs with the same basename cannot both land in app/src —
        rejected up front (fail-closed, no half-copy)."""
        (tmp_path / "myapp" / "src").mkdir(parents=True)
        (tmp_path / "staging" / "a").mkdir(parents=True)
        (tmp_path / "staging" / "b").mkdir()
        one = tmp_path / "staging" / "a" / "main.c"
        two = tmp_path / "staging" / "b" / "main.c"
        one.write_text("one")
        two.write_text("two")
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.add_sources(bridge, "myapp", [str(one), str(two)])
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "INVALID_FILES"
        assert "duplicate destination file name" in r["error"]["message"]
        assert bridge.calls == []

    async def test_add_sources_file_not_found(self, tmp_path):
        (tmp_path / "myapp" / "src").mkdir(parents=True)
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.add_sources(
            bridge, "myapp", [str(tmp_path / "nope" / "main.c")])
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "FILE_NOT_FOUND"
        assert bridge.calls == []

    async def test_add_sources_workspace_unknown(self, tmp_path):
        main = tmp_path / "main.c"
        main.write_text("x")
        bridge = FakeXsctBridge(workspace="")
        r = await ps_bsp.add_sources(bridge, "myapp", [str(main)])
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKSPACE_UNKNOWN"
        assert r["error"]["code"] == "ENV_ERROR"


# ── compile_app ELF-first / make-fallback behavior ───────────────────────────

class TestCompileApp:

    async def test_compile_app_skips_make_when_elf_exists(self, tmp_path):
        """app build already produced the ELF → make must not be invoked."""
        app = tmp_path / "myapp"
        (app / "Debug").mkdir(parents=True)
        elf = _write_elf(str(app / "Debug" / "app.elf"))
        bridge = FakeXsctBridge(results=[_OK], workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "success", r
        assert r["data"]["built"] is True
        assert r["data"]["elf"] == str(elf).replace("\\", "/"), r
        # exactly one eval (app build); no make step ran.
        assert len(bridge.calls) == 1, bridge.calls
        assert bridge.calls[0][0] == "app build -name myapp"

    async def test_compile_app_passes_defines_to_app_build(self, tmp_path):
        """D10: defines set via set_compiler_options must reach the build
        config (`app config -add define-compiler-symbols`, one call per
        symbol) before `app build` — not just be stored in _WS_DEFINES."""
        app = tmp_path / "myapp"
        (app / "src").mkdir(parents=True)      # needed for app discovery
        (app / "Debug").mkdir(parents=True)
        _write_elf(str(app / "Debug" / "app.elf"))
        bridge = FakeXsctBridge(results=[_OK, _OK, _OK], workspace=str(tmp_path))
        r = await ps_bsp.set_compiler_options(
            bridge, {"defines": "FAULT_INJECT=1 PROBE"})
        assert r["status"] == "success", r
        try:
            r = await ps_bsp.compile_app(bridge, "myapp")
            assert r["status"] == "success", r
            # one app config define call per symbol, then the plain build
            assert bridge.calls[0][0] == \
                "app config -name myapp -add define-compiler-symbols {FAULT_INJECT=1}", \
                bridge.calls[0][0]
            assert bridge.calls[1][0] == \
                "app config -name myapp -add define-compiler-symbols {PROBE}", \
                bridge.calls[1][0]
            assert bridge.calls[2][0] == "app build -name myapp", \
                bridge.calls[2][0]
            assert len(bridge.calls) == 3, bridge.calls
        finally:
            ps_bsp._WS_DEFINES.pop(str(tmp_path), None)

    async def test_compile_app_defines_are_workspace_scoped(self, tmp_path):
        """D10: defines configured for one workspace must not leak into a
        plain build in another workspace (keyed by workspace path)."""
        def _mk(ws):
            app = ws / "myapp"
            (app / "src").mkdir(parents=True)
            (app / "Debug").mkdir(parents=True)
            _write_elf(str(app / "Debug" / "app.elf"))
            return FakeXsctBridge(results=[_OK, _OK], workspace=str(ws))

        ws1 = tmp_path / "w1"
        ws1.mkdir()
        ws2 = tmp_path / "w2"
        ws2.mkdir()
        b1, b2 = _mk(ws1), _mk(ws2)

        r = await ps_bsp.set_compiler_options(b1, {"defines": "FAULT_INJECT"})
        assert r["status"] == "success", r
        try:
            await ps_bsp.compile_app(b1, "myapp")
            await ps_bsp.compile_app(b2, "myapp")
            assert b1.calls[0][0] == \
                "app config -name myapp -add define-compiler-symbols {FAULT_INJECT}", \
                b1.calls
            assert b1.calls[1][0] == "app build -name myapp", b1.calls
            # workspace w2: no defines configured → plain build only
            assert b2.calls[0][0] == "app build -name myapp", b2.calls
            assert len(b2.calls) == 1, b2.calls
        finally:
            ps_bsp._WS_DEFINES.pop(str(ws1), None)
            ps_bsp._WS_DEFINES.pop(str(ws2), None)

    async def test_compile_app_define_config_failure_is_fail_closed(
            self, tmp_path):
        """D10: if the app config define call fails, compile_app must return
        BUILD_FAILED and never run the build."""
        app = tmp_path / "myapp"
        (app / "src").mkdir(parents=True)
        (app / "Debug").mkdir(parents=True)
        _write_elf(str(app / "Debug" / "app.elf"))
        bridge = FakeXsctBridge(results=[_err()], workspace=str(tmp_path))
        r = await ps_bsp.set_compiler_options(bridge, {"defines": "PROBE"})
        assert r["status"] == "success", r
        try:
            r = await ps_bsp.compile_app(bridge, "myapp")
            assert r["status"] == "error"
            assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
            assert len(bridge.calls) == 1, \
                "no app build may run after a failed define config"
        finally:
            ps_bsp._WS_DEFINES.pop(str(tmp_path), None)

    async def test_compile_app_make_fallback_no_make_found(self, tmp_path,
                                                           monkeypatch):
        """No ELF from app build and make.exe unresolvable → fail-closed."""
        bridge = FakeXsctBridge(results=[_OK], workspace=str(tmp_path))
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: None)
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        # no make attempted when make.exe cannot be located.
        assert len(bridge.calls) == 1, bridge.calls

    async def test_compile_app_make_fallback_invokes_full_make_path(
            self, tmp_path, monkeypatch):
        """make fallback must exec make.exe by full resolved path, not PATH."""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)
        app = tmp_path / "myapp"

        class _MakeBridge(FakeXsctBridge):
            async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
                self.calls.append((tcl, timeout_s, tolerate_stderr))
                if "exec" in tcl:
                    (app / "Debug").mkdir(parents=True, exist_ok=True)
                    _write_elf(str(app / "Debug" / "app.elf"))
                return dict(_OK)

        bridge = _MakeBridge(workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "success", r
        assert r["data"]["built"] is True
        assert len(bridge.calls) == 2, bridge.calls
        make_tcl = bridge.calls[1][0]
        assert f"exec {{{make_exe}}}" in make_tcl
        assert f"cd {{" in make_tcl
        assert "myapp/Debug" in make_tcl.replace("\\", "/")

    async def test_compile_app_make_fallback_uses_explicit_all_target_f12(
            self, tmp_path, monkeypatch):
        """B13-F12 修复轮#12: 回退 make 必须显式传 `all` 目标——Vitis 生成的
        Debug/makefile 里 `-include $(C_DEPS)` 引入的 .d 文件带显式 src/*.o
        目标，裸 make 默认目标变成第一个 .o（只建一个对象即 exit 0、无 ELF、
        无输出）。"""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)

        class _MakeBridge(FakeXsctBridge):
            async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
                self.calls.append((tcl, timeout_s, tolerate_stderr))
                if "exec" in tcl:
                    d = os.path.join(self.workspace, "myapp", "Debug")
                    os.makedirs(d, exist_ok=True)
                    _write_elf(os.path.join(d, "app.elf"))
                return dict(_OK)

        bridge = _MakeBridge(workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "success", r
        make_tcl = bridge.calls[1][0]
        assert f"exec {{{make_exe}}} all 2>@1" in make_tcl

    async def test_compile_app_no_elf_surfaces_make_output_f12(
            self, tmp_path, monkeypatch):
        """B13-F12 修复轮#12: make rc=0 但无 ELF 时（默认目标陷阱类），make
        输出经 __MAKE_OUTPUT_BEGIN__/END 标记带回，真实错误出现在 BUILD_FAILED
        消息里，而不是裸的 "no ELF produced"。"""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)
        # make "succeeds" (default-target trap would exit 0) but prints the
        # real diagnostic and produces no ELF.
        make_ok = {"status": "success", "data":
                   "__MAKE_OUTPUT_BEGIN__\n"
                   "make: 'src/main.o' is up to date.\n"
                   "arm-none-eabi-gcc: error: undefined reference to 'tcp_tmr'\n"
                   "__MAKE_OUTPUT_END__"}
        bridge = FakeXsctBridge(results=[_OK, make_ok], workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        msg = r["error"]["message"]
        assert "no ELF produced" in msg
        assert "undefined reference to 'tcp_tmr'" in msg
        assert "__MAKE_OUTPUT_BEGIN__" not in msg  # 标记被剥离

    async def test_compile_app_make_fallback_includes_full_output(
            self, tmp_path, monkeypatch):
        """D-C: a MAKE_FALLBACK build failure must return the FULL make/compiler
        output (not a single line). Returns BUILD_FAILED with reason_code and
        the truncation/total-length details."""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)
        # A multi-line make failure (compiler errors + make recipe error).
        full_output = (
            "Building file: ../src/main.c\n"
            "../src/main.c:5: error: 'zdx' undeclared (first use in this "
            "function)\n"
            "arm-none-eabi-gcc: fatal error: no input files\n"
            "make.exe: *** [Debug/subdir.mk:6] Error 1\n")
        make_err = {"status": "error", "error": {
            "code": "XSDM_EVAL_ERROR", "message": full_output,
            "details": {"reason_code": "XSDM_TCL_ERROR"}}}
        bridge = FakeXsctBridge(results=[_OK, make_err], workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        msg = r["error"]["message"]
        assert "make in Debug failed" in msg
        assert "Building file: ../src/main.c" in msg
        assert "main.c:5: error" in msg
        assert "Error 1" in msg
        assert "TRUNCATED" not in msg  # under cap, no truncation marker
        assert r["error"]["details"]["build_output_truncated"] is False

    async def test_compile_app_make_fallback_truncates_long_output(
            self, tmp_path, monkeypatch):
        """D-C: a very long complete compiler output is kept to a cap but the
        truncation marker + total length are explicitly reported."""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)
        long_lines = "\n".join(
            f"gcc: line {i}: undeclared identifier 'zzz{i}'" for i in range(1, 3000))
        full_output = f"Building file: ../src/main.c\n{long_lines}\nmake: *** Error 1\n"
        make_err = {"status": "error", "error": {
            "code": "XSDM_EVAL_ERROR", "message": full_output,
            "details": {"reason_code": "XSDM_TCL_ERROR"}}}
        bridge = FakeXsctBridge(results=[_OK, make_err], workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        msg = r["error"]["message"]
        assert "Building file: ../src/main.c" in msg
        assert "TRUNCATED:" in msg
        assert r["error"]["details"]["build_output_truncated"] is True
        total = r["error"]["details"]["build_output_len"]
        assert total > ps_bsp._MAX_BUILD_OUTPUT_LEN
        # the marker reports kept/total.
        assert f"{ps_bsp._MAX_BUILD_OUTPUT_LEN}/{total}" in msg

    async def test_compile_app_app_build_failure_includes_full_output(
            self, tmp_path):
        """D-C (item #1): `app build failed` must carry the FULL build/link
        output, not one terse line. An undefined-reference link error is the
        actionable cause and must reach the caller."""
        full_output = (
            "Building file: ../src/main.c\n"
            "arm-none-eabi-gcc: error: main.c:5: undefined reference to "
            "`XUartPs_Initialize'\n"
            "collect2: error: ld returned 1 exit status\n")
        app_err = {"status": "error", "error": {
            "code": "XSDM_EVAL_ERROR", "message": full_output,
            "details": {"reason_code": "XSDM_TCL_ERROR"}}}
        bridge = FakeXsctBridge(results=[app_err], workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        msg = r["error"]["message"]
        assert "app build failed" in msg
        assert "undefined reference to `XUartPs_Initialize'" in msg
        assert "ld returned 1 exit status" in msg
        assert r["error"]["details"]["build_output_truncated"] is False

    async def test_compile_app_no_elf_surfaces_build_output(
            self, tmp_path, monkeypatch):
        """D-C (item #1): when the build "succeeds" (no Tcl error) but produces
        no ELF, the real link error still in the captured stdout must be
        returned so the caller can locate it — not a bare one-line conclusion.
        This is the exact BSP-lib-incomplete scenario (an undefined reference
        to a driver symbol that link never resolves)."""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)
        # app build returns success (no ELF), make fallback returns success with
        # the link error on stdout but STILL produces no ELF.
        no_elf_build = {"status": "success", "data": ""}
        make_output = ("cd .../Debug\n"
                       "arm-none-eabi-gcc: error: ../src/main.c:160: undefined "
                       "reference to `XUartPs_Initialize'\n"
                       "collect2: error: ld returned 1 exit status\n")
        make_ok = {"status": "success", "data": make_output}
        bridge = FakeXsctBridge(results=[no_elf_build, make_ok],
                                workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        assert "no ELF produced after build" in r["error"]["message"]
        # the captured link detail must surface.
        assert "undefined reference to `XUartPs_Initialize'" in \
            r["error"]["message"]
        assert "collect2: error: ld returned 1 exit status" in \
            r["error"]["message"]
        assert r["error"]["details"]["build_output_len"] == len(make_output)

    async def test_compile_app_no_elf_no_output_keeps_bare_message(
            self, tmp_path, monkeypatch):
        """When the build produced no ELF AND left no usable stdout (no detail
        to surface), the message stays a clean base line — no crash, no
        fabricated detail."""
        make_exe = "D:/Xilinx/Vivado/2023.1/gnuwin/bin/make.exe"
        monkeypatch.setattr(ps_bsp, "_find_make", lambda: make_exe)
        bridge = FakeXsctBridge(results=[_OK, _OK], workspace=str(tmp_path))
        r = await ps_bsp.compile_app(bridge, "myapp")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "BUILD_FAILED"
        assert r["error"]["message"] == \
            "no ELF produced after build for app 'myapp'"

    async def test_find_make_resolves_from_vivado_root(self, monkeypatch):
        """make.exe resolution honors $VIVADO_ROOT over the default root."""
        monkeypatch.delenv("VIVADO_EXEC", raising=False)
        monkeypatch.setenv("VIVADO_ROOT", "C:/tools/Vivado/2023.1")
        monkeypatch.setattr(
            os.path, "isfile",
            lambda p: p.replace("\\", "/").endswith("gnuwin/bin/make.exe"))
        monkeypatch.setattr(shutil, "which", lambda name: None)
        expected = os.path.normpath(
            "C:/tools/Vivado/2023.1/gnuwin/bin/make.exe")
        assert ps_bsp._find_make() == expected


# ── workspace discovery via bridge.workspace ─────────────────────────────────

class TestWorkspaceStatus:

    async def test_get_bsp_status_empty(self, tmp_path):
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.get_bsp_status(bridge)
        assert r["status"] == "success"
        assert r["data"]["bsps"] == []
        assert r["data"]["count"] == 0

    async def test_get_bsp_status_lists_apps(self, tmp_path):
        (tmp_path / "myapp").mkdir()
        (tmp_path / "myapp" / "src").mkdir()
        (tmp_path / "someplatform").mkdir()
        (tmp_path / "someplatform" / "hw").mkdir()
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.get_bsp_status(bridge)
        assert r["status"] == "success"
        assert r["data"]["bsps"] == ["myapp"], r
        assert r["data"]["count"] == 1

    async def test_get_build_status_reports_built_elf(self, tmp_path):
        (tmp_path / "myapp").mkdir()
        (tmp_path / "myapp" / "src").mkdir()
        debug = tmp_path / "myapp" / "Debug"
        debug.mkdir()
        (debug / "myapp.elf").write_bytes(b"\x7fELF" + b"\x00" * 48)
        bridge = FakeXsctBridge(workspace=str(tmp_path))
        r = await ps_bsp.get_build_status(bridge)
        assert r["status"] == "success"
        app = r["data"]["apps"][0]
        assert app["name"] == "myapp" and app["built"] is True
        assert app["elf"].replace("\\", "/").endswith("myapp/Debug/myapp.elf")

    async def test_status_requires_workspace(self):
        bridge = FakeXsctBridge(workspace="")
        r = await ps_bsp.get_bsp_status(bridge)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKSPACE_UNKNOWN"
        assert r["error"]["code"] == "ENV_ERROR"


# ── read_elf_info (pure Python ELF parsing) ──────────────────────────────────

class TestReadElf:

    async def test_read_elf_info_valid_elf(self, tmp_path):
        elf = _write_elf(str(tmp_path / "a.elf"))
        bridge = FakeXsctBridge()
        r = await ps_bsp.read_elf_info(bridge, elf)
        assert r["status"] == "success"
        d = r["data"]
        assert d["magic_valid"] is True
        assert d["elf_class"] == "ELFCLASS32"
        assert d["data_encoding"] == "LSB"
        assert d["machine"] == 40
        assert d["type"] == 2
        assert d["entry_point"] == "0x10000000"

    async def test_read_elf_info_not_elf(self, tmp_path):
        bad = tmp_path / "not_elf.bin"
        bad.write_bytes(b"this is not an elf file at all.........")
        bridge = FakeXsctBridge()
        r = await ps_bsp.read_elf_info(bridge, str(bad))
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "ELF_INVALID"

    async def test_read_elf_info_missing(self, tmp_path):
        bridge = FakeXsctBridge()
        r = await ps_bsp.read_elf_info(bridge, str(tmp_path / "no.elf"))
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "ELF_NOT_FOUND"

    async def test_read_elf_info_path_escape(self):
        bridge = FakeXsctBridge()
        r = await ps_bsp.read_elf_info(bridge, "D:/../../evil.elf")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "PATH_ESCAPE"


# ── XsctBridge tolerant-stderr / catch-wrapper ───────────────────────────────

class TestTolerantStderrParse:

    def _bridge(self):
        return XsctBridge()

    async def test_catch_wrap_produces_marker(self):
        wrapped = _catch_wrap("app build -name myapp")
        assert wrapped == (
            'if {[catch {app build -name myapp} __xsct_err]} '
            f'{{ puts "{_TCLERR_MARKER}$__xsct_err" }}')

    async def test_parse_tolerate_stderr_success_ignores_noise(self):
        out = ("__XSCT_BEGIN_0__\n"
               "some compiler note\n"
               "arm-none-eabi-ar: creating lib.a\n"
               "__XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "success"

    async def test_parse_tolerate_stderr_catches_tcl_error(self):
        out = ("__XSCT_BEGIN_0__\n"
               f"{_TCLERR_MARKER}No active platform present, please create one.\n"
               "__XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "error"
        assert "No active platform" in r["error"]["message"]
        assert r["error"]["details"]["reason_code"] == "XSDM_TCL_ERROR"

    async def test_parse_tolerate_stderr_flags_error_lines(self):
        out = ("__XSCT_BEGIN_0__\n"
               "ERROR: app build failed\n"
               "__XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "error"
        assert "app build failed" in r["error"]["message"]

    async def test_parse_tolerate_stderr_strips_prompt_prefixes(self):
        """Real interactive xsct prefixes every stdout line with `xsct% `.

        The markers and data must still be detected after the prompt prefix
        is stripped (this is the actual raw stream the bridge receives from
        a live xsct process, verified against Xilinx xsct.bat 2023.1).
        """
        out = ("xsct% __XSCT_BEGIN_0__\r\n"
               "xsct% some compiler note\r\n"
               "xsct% arm-none-eabi-ar: creating lib.a\r\n"
               "xsct% __XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "success"
        assert "some compiler note" in r["data"]
        assert "__XSCT_" not in r["data"]

    async def test_parse_tolerate_stderr_tcl_error_prompt_prefixed(self):
        """A prompt-prefixed __XSCT_TCLERR__ line still reports a Tcl error."""
        out = ("xsct% __XSCT_BEGIN_0__\r\n"
               f"xsct% {_TCLERR_MARKER}No active platform present, please create one.\r\n"
               "xsct% __XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "error"
        assert "No active platform" in r["error"]["message"]
        assert r["error"]["details"]["reason_code"] == "XSDM_TCL_ERROR"

    async def test_parse_tolerate_stderr_error_line_prompt_prefixed(self):
        """A prompt-prefixed ERROR: line still reports an error."""
        out = ("xsct% __XSCT_BEGIN_0__\r\n"
               "xsct% ERROR: app build failed\r\n"
               "xsct% __XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "error"
        assert "app build failed" in r["error"]["message"]

    async def test_parse_tolerate_stderr_preserves_full_make_error(self):
        """D-C: a failed ``exec make`` produces a MULTI-line Tcl error. The
        parser must keep every line after the __XSCT_TCLERR__ marker, not just
        the first — otherwise the compiler error detail (the actual cause) is
        swallowed and the caller only sees ``'Building file: ../src/main.c'``."""
        out = ("xsct% __XSCT_BEGIN_0__\r\n"
               f"{_TCLERR_MARKER}Building file: ../src/main.c\r\n"
               "xsct% ../src/main.c:5: error: 'zdx' undeclared (first use in "
               "this function)\r\n"
               "xsct% make.exe: *** [Debug/subdir.mk:6] Error 1\r\n"
               "xsct% __XSCT_END_0__")
        r = self._bridge()._parse_tolerate_stderr(out, "__XSCT_BEGIN_0__",
                                                  "__XSCT_END_0__")
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "XSDM_TCL_ERROR"
        assert "Building file: ../src/main.c" in r["error"]["message"]
        assert "main.c:5: error" in r["error"]["message"]
        assert "Error 1" in r["error"]["message"]
