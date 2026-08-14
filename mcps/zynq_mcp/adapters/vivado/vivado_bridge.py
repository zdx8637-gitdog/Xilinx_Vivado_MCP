"""vivado_bridge.py — VivadoTclBridge: direct asyncio Tcl bridge to vivado.exe.

Vivado (`vivado -mode tcl`) is a bare Tcl shell, like XSDB. The old PL bridge
tools went through two layers of stdio (``zynq_mcp → VivadoAdapter(stdio) →
old Xilinx_Vivado_MCP/server.py → vivado.exe``) whose MCP stdio transport
breaks on long (15+ min, silent) synthesis runs. XsdbBridge's direct
stdin/stdout sentinel pattern survives 30 min, so this bridge reuses the same
``_TclShellBridge`` base (shared with XsdbBridge/XsctBridge) to drive
vivado.exe directly — no old-MCP middle layer.

Differences from XSDB:
  - executable is ``vivado.exe`` (NOT the .bat wrapper) so there is no
    ``cmd /c`` indirection.
  - the Tcl prompt is ``% `` (not ``xsdb% ``); configured via
    ``_prompt_prefixes`` on the shared base.
  - startup banner is Vivado copyright text; readiness = a few KB of output
    read (no specific string waited on) — the shared ``_read_banner`` already
    does this.
  - Vivado reports Tcl errors on stdout as ``ERROR: ...`` and writes routine
    noise to stderr, so stderr is NOT fatal (``_stderr_is_fatal = False``).
  - Vivado re-prints its banner before every command through a pipe; banner
    lines are dropped from command data by ``_keep_data_line`` (mirrors the
    old MCP's ``send_tcl`` filter).

Lifecycle: start() -> [eval() x N] -> stop()

Error contract (shared with XsdbBridge/XsctBridge):
  - eval() returns {"status": "error", ...} when the Tcl command itself failed
    (output contains an ERROR: line).
  - eval() raises VivadoBridgeError when the bridge itself failed (eval
    timeout, mid-command process death, missing sentinel parse error).
  - a dead/stopped process makes eval() return an error dict immediately
    (fail-closed).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import (
    _TclShellBridge,
    _full_path_variants,
    REASON_PROCESS_DEAD,
    REASON_WRITE_FAILED,
)

logger = logging.getLogger("zynq_mcp.vivado.vivado_bridge")

# Prefer vivado.exe (direct, no cmd /c wrapper). Fall back to the .bat only
# when the .exe is absent.
_DEFAULT_VIVADO_BIN = r"D:/Xilinx/Vivado/2023.1/bin"
_VIVADO_CANDIDATES = ("vivado.exe", "vivado.bat")

# Synthesis/implementation can legitimately run 15-30 min; the bridge must
# wait as long as vivado itself is alive. Short query tools pass their own
# smaller timeout_s to eval().
DEFAULT_VIVADO_TIMEOUT = 3600.0

# Bound for the one-time initialization commands in start(). Vivado cold
# start can take a minute or two, but a hung start must fail within a few
# minutes, not the 1h default.
INIT_TIMEOUT = 300.0

# Startup/prompt timing constants reused from the shared base's defaults.
BANNER_READ_TIMEOUT = 5.0

# A vendor launcher can terminate transiently before the first initialization
# command (O7 R1 observed a Windows pipe "Connection lost").  Retrying is safe
# only here: no user Tcl or project command has run, init commands are
# idempotent, and stop() proves the failed process is gone before relaunch.
START_ATTEMPTS = 2


class VivadoBridgeError(Exception):
    """Raised when the bridge itself fails (process died, timeout, parse error)."""

    def __init__(self, message: str, *, reason_code: str | None = None):
        self.reason_code = reason_code
        super().__init__(message)


def _find_vivado_in_dir(dirpath: str) -> str | None:
    """Return the first existing vivado variant inside ``dirpath``."""
    for variant in _VIVADO_CANDIDATES:
        p = os.path.join(dirpath, variant)
        if os.path.isfile(p):
            return p
    return None


def find_vivado() -> str | None:
    """Resolve the vivado executable path.

    Search order:
      1. a VIVADO_EXEC env var holding a full path that exists
      2. ``$VIVADO_ROOT/bin/vivado.exe`` (or .bat fallback)
      3. the default install dir ``D:/Xilinx/Vivado/2023.1/bin/vivado.exe``
      4. ``shutil.which("vivado")`` on PATH

    Returns the executable path, or None if not found. Never raises.
    """
    val = os.environ.get("VIVADO_EXEC", "").strip()
    if val:
        for variant in _full_path_variants(val, "vivado"):
            if os.path.isfile(variant):
                return variant

    root = os.environ.get("VIVADO_ROOT", "").strip()
    if root:
        hit = _find_vivado_in_dir(os.path.join(root, "bin"))
        if hit:
            return hit

    hit = _find_vivado_in_dir(_DEFAULT_VIVADO_BIN)
    if hit:
        return hit

    found = shutil.which("vivado")
    return found if found else None


class VivadoTclBridge(_TclShellBridge):
    """Manages a persistent ``vivado -mode tcl`` subprocess.

    Like XsdbBridge, commands are written to the child's stdin and completion
    is detected with unique sentinel markers echoed around each command via
    ``puts``. Vivado is always a Tcl shell, so ``-mode tcl`` is passed at
    launch (no ``-interactive`` flag — that is xsdb-specific).

    Lifecycle: start() -> [eval() x N] -> stop()
    """

    _tool_name = "vivado"
    _default_timeout: float = DEFAULT_VIVADO_TIMEOUT
    _error_type: type = VivadoBridgeError
    # Vivado is always Tcl — no ``-interactive`` flag (xsdb-specific).
    _extra_launch_args: list[str] | None = ["-mode", "tcl"]
    # Vivado's Tcl prompt is ``% `` (not ``vivado% ``); strip both so real and
    # echoed output stay clean regardless of the exact prompt string.
    _prompt_prefixes: tuple[str, ...] = ("% ", "vivado% ")
    # Vivado reports Tcl errors on stdout; stderr holds routine noise, so it
    # must not be fatal.
    _stderr_is_fatal: bool = False

    # Initialization commands run once after launch (fail-closed: if any of
    # them fails the bridge refuses to start).
    _init_commands: tuple[str, ...] = (
        "set_msg_config -suppress -id {Common 17-54}",
        "set_param general.maxThreads 4",
    )

    def __init__(self, vivado_path: str | None = None):
        super().__init__(executable=vivado_path)

    def _resolve_executable(self) -> str | None:
        return find_vivado()

    def _keep_data_line(self, stripped: str) -> bool:
        """Drop Vivado's banner reprints and noise lines from command data.

        Vivado re-prints its startup banner before every command when driven
        through a pipe; these lines (``****`` / ``** ...`` / ``# ...`` /
        ``Vivado v...SW Build``) are the same ones the old MCP's ``send_tcl``
        filter removes, and they never carry command output.
        """
        if stripped.startswith("#"):
            return False
        if stripped.startswith("****") or stripped.startswith("** ") \
                or stripped == "**":
            return False
        if "Vivado v" in stripped and ("SW Build" in stripped
                                       or stripped.startswith("****")):
            return False
        return True

    async def start(self) -> None:
        """Launch vivado -mode tcl and run the initialization commands."""
        if self.ready:
            return
        last_message = ""
        last_reason = None
        for attempt in range(1, START_ATTEMPTS + 1):
            await self._launch()
            # Initialization: suppress benign message noise and cap threads.
            # Fail-closed: a failed init command means the shell is not usable.
            # Bounded by INIT_TIMEOUT so a hung cold start fails within minutes.
            retryable = False
            for cmd in self._init_commands:
                try:
                    res = await self.eval(cmd, timeout_s=INIT_TIMEOUT)
                except VivadoBridgeError as exc:
                    last_message = str(exc)
                    last_reason = exc.reason_code
                    proc = self._proc
                    if proc is not None and proc.returncode is None:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(proc.wait()), timeout=0.25)
                        except asyncio.TimeoutError:
                            pass
                    # Missing sentinels after EOF are another form of a
                    # pre-command launcher death.  Retry only when the process
                    # is actually gone; a live-channel parse defect fails
                    # closed without relaunch.
                    if not self.ready:
                        last_reason = last_reason or REASON_PROCESS_DEAD
                        retryable = True
                    break
                if res["status"] == "success":
                    continue
                err = res.get("error", {})
                last_message = str(err.get("message") or "unknown init error")
                last_reason = (err.get("details") or {}).get("reason_code")
                retryable = last_reason in {
                    REASON_PROCESS_DEAD, REASON_WRITE_FAILED,
                }
                break
            else:
                return

            await self.stop()
            if retryable and attempt < START_ATTEMPTS:
                logger.warning(
                    "vivado launcher failed before user Tcl (attempt %s/%s, %s); "
                    "cleanly relaunching once",
                    attempt, START_ATTEMPTS, last_reason)
                continue
            raise VivadoBridgeError(
                f"vivado init command failed after {attempt} attempt(s): "
                f"{last_message}", reason_code=last_reason)
