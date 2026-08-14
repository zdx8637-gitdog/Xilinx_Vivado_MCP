"""xsct_bridge.py — XsctBridge: persistent interactive XSCT subprocess.

XSCT (Xilinx Software Command-Line Tool) is a bare Tcl shell used for
software-platform operations: import hardware, platform/BSP/app creation, and
build. Like XSDB it uses the same sentinel-marker interactive pattern (shared
base class in xsdb_bridge.py), but with a longer default eval timeout (builds
can be slow) and an optional workspace (``setws``) on start. XsctBridge has
no hw_connected state.

Lifecycle: start() -> [eval() x N] -> stop()
"""
from __future__ import annotations

import asyncio

from mcps.zynq_mcp.adapters.xsct import templates
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import (
    _DEFAULT_VITIS_BIN,
    _TclShellBridge,
    _resolve_executable,
    XSDM_EVAL_ERROR,
    REASON_TCL_ERROR,
)

DEFAULT_XSCT_TIMEOUT = 60.0


class XsctBridgeError(Exception):
    """Raised when the bridge itself fails (process died, timeout, parse error)."""


# Marker emitted (on stdout) by the catch-wrapper in XsctBridge.eval when a
# command raises a Tcl error. The message follows on the same line.
_TCLERR_MARKER = "__XSCT_TCLERR__"


def _catch_wrap(tcl: str) -> str:
    """Wrap a Tcl command so its Tcl error is captured onto stdout.

    XSCT commands (platform create/generate, app create/build) write
    routine compiler/build noise to stderr, and XSCT also reports Tcl
    errors on stderr — the shared sentinel parser cannot distinguish them.
    This wrapper captures any Tcl error into a stdout marker so the caller
    can tell success from failure regardless of stderr noise.

    Returns a single-line `if {[catch {...} __xsct_err]} {...}` statement.
    Only safe for brace-free commands (our generated commands are).
    """
    return (f'if {{[catch {{{tcl}}} __xsct_err]}} '
            f'{{ puts "{_TCLERR_MARKER}$__xsct_err" }}')


def find_xsct() -> str | None:
    """Resolve the xsct executable using the standard search order."""
    return _resolve_executable("xsct", ("XSCT_EXEC",), _DEFAULT_VITIS_BIN)


class XsctBridge(_TclShellBridge):
    """Manages a persistent xsct subprocess for software platform operations.

    xsct is used for: import_hw, platform create, bsp create, app create,
    build. Unlike xsdb, xsct operations are typically one-shot batch commands,
    not persistent interactive sessions. For consistency we still use the
    same interactive + sentinel pattern.
    """

    _tool_name = "xsct"
    _default_timeout: float = DEFAULT_XSCT_TIMEOUT
    _error_type: type = XsctBridgeError
    _extra_launch_args: list[str] | None = ["-interactive"]

    def __init__(self, xsct_path: str | None = None):
        super().__init__(executable=xsct_path)
        self.workspace: str | None = None

    def _resolve_executable(self) -> str | None:
        return find_xsct()

    async def start(self, workspace: str | None = None) -> None:
        """Launch xsct. If workspace is non-empty, run ``setws <workspace>``.

        The workspace is remembered on ``self.workspace`` so BSP/Build
        status queries can inspect the workspace on the host filesystem.
        """
        if self.ready:
            return
        await self._launch()
        if workspace:
            self.workspace = workspace
            res = await self.eval(templates.setws(workspace))
            if res["status"] != "success":
                await self.stop()
                raise XsctBridgeError(
                    f"setws {workspace} failed: {res['error']['message']}")

    async def eval(self, tcl: str, timeout_s: float | None = None,
                   tolerate_stderr: bool = False) -> dict:
        """Send a Tcl command to xsct.

        When ``tolerate_stderr`` is True (BSP/Build commands that write
        compiler noise to stderr), the command is wrapped in a catch so a
        Tcl error is captured onto stdout as ``__XSCT_TCLERR__<msg>`` and
        parsed by ``_parse_tolerate_stderr`` (stderr is ignored).
        """
        if tolerate_stderr:
            tcl = _catch_wrap(tcl)
        return await super().eval(tcl, timeout_s=timeout_s,
                                  tolerate_stderr=tolerate_stderr)

    def _parse_tolerate_stderr(self, output: str, begin: str, end: str) -> dict:
        """Parse a catch-wrapped eval result, ignoring stderr noise.

        A ``__XSCT_TCLERR__`` line in the stdout data means the command
        failed; ``ERROR:``/``Error:`` lines also count as failures. stderr
        is ignored (compilers write routine noise there during builds).

        Real interactive xsct prefixes every stdout line with its prompt
        (``xsct% <output>``), so prompt prefixes are stripped from each
        line before marker/error matching — the same rule the base class
        ``_parse_output`` already applies (without this, the ``xsct% `` prefix
        makes the sentinel markers and TCLERR/ERROR markers undetectable).
        """
        prefixes = self._prompt_prefixes
        if prefixes is None:
            prefixes = (f"{self._tool_name}% ",)
        lines = []
        for ln in output.splitlines():
            s = ln.strip()
            while True:
                hit = False
                for pre in prefixes:
                    if s.startswith(pre):
                        s = s[len(pre):].strip()
                        hit = True
                        break
                if not hit:
                    break
            lines.append(s)
        b_idx = -1
        e_idx = -1
        for i, s in enumerate(lines):
            if s == begin:
                b_idx = i
            elif s == end:
                e_idx = i
        if b_idx == -1 or e_idx == -1 or e_idx <= b_idx:
            raise self._error_type(
                f"parse error: sentinel markers missing in xsct output")
        data_lines = lines[b_idx + 1:e_idx]
        data = "\n".join(data_lines).strip()

        for s in data_lines:
            if s.startswith(_TCLERR_MARKER):
                return self._error_dict(
                    XSDM_EVAL_ERROR, s[len(_TCLERR_MARKER):].strip(),
                    REASON_TCL_ERROR)
        error_lines = [s for s in data_lines
                       if s.startswith("ERROR:")
                       or s.startswith("Error:")]
        if error_lines:
            return self._error_dict(
                XSDM_EVAL_ERROR, "; ".join(error_lines), REASON_TCL_ERROR)
        return {"status": "success", "data": data}
