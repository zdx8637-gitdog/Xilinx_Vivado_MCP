"""xsdb_bridge.py — XsdbBridge: persistent interactive XSDB subprocess.

XSDB (Xilinx System Debugger) is a bare Tcl shell used for JTAG operations
(connect, targets, download, reset, memory access). Unlike the Vivado MCP
adapter, there is no MCP protocol layer — commands are written to the child's
stdin and completion is detected with unique sentinel markers echoed around
each Tcl command via ``puts``.

The ``_TclShellBridge`` base class in this module is shared with XsctBridge
(xsct_bridge.py): same interactive + sentinel pattern, no duplicated logic.

Lifecycle: start() -> [eval() x N] -> stop()

Error contract (shared with XsctBridge):
  - eval() returns {"status": "error", ...} when the Tcl command itself failed
    (output contains an ERROR: line, or non-empty stderr).
  - eval() raises the bridge Error type when the bridge itself failed
    (eval timeout, mid-command process death, missing sentinel parse error).
  - A dead/stopped process makes eval() return an error dict immediately
    (fail-closed: never hang, never report a false success).
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil

from mcps.zynq_mcp.adapters.xsct import templates

logger = logging.getLogger("zynq_mcp.xsct.xsdb_bridge")

_DEFAULT_VITIS_BIN = r"D:/Xilinx/Vitis/2023.1/bin"

BANNER_READ_TIMEOUT = 5.0
STDERR_READ_TIMEOUT = 0.1
EXIT_GRACE = 3.0
TERMINATE_GRACE = 2.0
KILL_GRACE = 2.0
STDIN_WRITE_GRACE = 1.0
DEFAULT_EVAL_TIMEOUT = 30.0

XSDM_EVAL_ERROR = "XSDM_EVAL_ERROR"
REASON_PROCESS_DEAD = "XSDM_PROCESS_DEAD"
REASON_TCL_ERROR = "XSDM_TCL_ERROR"
REASON_STDERR_OUTPUT = "XSDM_STDERR_OUTPUT"
REASON_WRITE_FAILED = "XSDM_WRITE_FAILED"


class XsdbBridgeError(Exception):
    """Raised when the bridge itself fails (process died, timeout, parse error)."""


def _candidate_paths(name: str) -> list[str]:
    """Executable name variants to probe (xsdb.bat / xsdb / xsdb.exe on Windows)."""
    if os.name == "nt":
        return [f"{name}.bat", name, f"{name}.exe"]
    return [name]


def _find_in_dir(dirpath: str, name: str) -> str | None:
    """Return the first existing variant of ``name`` inside ``dirpath``."""
    for variant in _candidate_paths(name):
        p = os.path.join(dirpath, variant)
        if os.path.isfile(p):
            return p
    return None


def _full_path_variants(path: str, name: str) -> list[str]:
    """Env-var full paths: use as-is if the basename has an extension, else
    probe the name variants (so `.../xsdb` finds `.../xsdb.bat` on Windows)."""
    if os.path.splitext(os.path.basename(path))[1]:
        return [path]
    return [path + variant for variant in _candidate_paths(name)]


def _resolve_executable(name: str, env_vars: tuple[str, ...],
                        default_dir: str) -> str | None:
    """Resolve the xsdb/xsct executable path.

    Search order:
      1. an env var (XSDM_EXEC/XSDB_EXEC for xsdb, XSCT_EXEC for xsct) holding
         a full path that exists
      2. ``$VITIS_ROOT/bin/<name>``
      3. the default install dir ``D:/Xilinx/Vitis/2023.1/bin/<name>``
      4. ``shutil.which(<name>)`` on PATH

    Returns the executable path, or None if not found. Never raises.
    """
    for var in env_vars:
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        for variant in _full_path_variants(val, name):
            if os.path.isfile(variant):
                return variant

    root = os.environ.get("VITIS_ROOT", "").strip()
    if root:
        hit = _find_in_dir(os.path.join(root, "bin"), name)
        if hit:
            return hit

    hit = _find_in_dir(default_dir, name)
    if hit:
        return hit

    found = shutil.which(name)
    return found if found else None


def find_xsdb() -> str | None:
    """Resolve the xsdb executable using the standard search order."""
    return _resolve_executable("xsdb", ("XSDM_EXEC", "XSDB_EXEC"),
                               _DEFAULT_VITIS_BIN)


def _build_launch_cmd(exe_path: str, extra_args: list[str] | None = None) -> list[str]:
    """On Windows, .bat/.cmd wrappers must run under cmd.exe /d /c.

    extra_args are appended AFTER the executable path.  XsdbBridge uses
    ``["-interactive"]`` so that command output is visible through the pipe
    (without it xsdb buffers all command results internally).
    """
    if os.name == "nt" and exe_path.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd.exe", "/d", "/c", exe_path]
        if extra_args:
            cmd.extend(extra_args)
        return cmd
    cmd = [exe_path]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def _vendor_subprocess_env() -> dict[str, str]:
    """Return a vendor-safe child environment without mutating the server.

    Codex/plugin MCP launchers may intentionally provide a narrow explicit
    environment.  Xilinx's Windows ``loader.bat`` exits silently with code 1
    when ``PROCESSOR_ARCHITECTURE`` is absent, before Vivado can emit its
    banner.  Restore only the core Windows process variables required by the
    vendor launcher; preserve every value the host did provide.
    """
    env = os.environ.copy()
    if os.name != "nt":
        return env
    arch = (env.get("PROCESSOR_ARCHITEW6432")
            or platform.machine() or "AMD64")
    env.setdefault("PROCESSOR_ARCHITECTURE", arch)
    system_root = (env.get("SystemRoot") or env.get("WINDIR")
                   or os.path.join(env.get("SystemDrive", "C:"), "Windows"))
    env.setdefault("SystemRoot", system_root)
    env.setdefault("WINDIR", system_root)
    env.setdefault("ComSpec", os.path.join(system_root, "System32", "cmd.exe"))
    return env


class _TclShellBridge:
    """Shared mechanics for a persistent interactive Tcl-shell bridge.

    Not for direct use; XsdbBridge and XsctBridge subclass it.
    """

    _tool_name = "xsdb"
    _default_timeout: float = DEFAULT_EVAL_TIMEOUT
    _error_type: type = XsdbBridgeError
    # Prompt prefix(es) stripped from the front of each data line. None means
    # the tool-name default (``<tool_name>% ``). VivadoTclBridge overrides this
    # because Vivado's Tcl prompt is ``% `` (not ``vivado% ``).
    _prompt_prefixes: tuple[str, ...] | None = None
    # Some shells (Vivado) write routine noise to stderr but report Tcl errors
    # on stdout; for them stderr must not be fatal. Default True keeps the
    # XSDB/XSCT fail-closed behavior.
    _stderr_is_fatal: bool = True

    def __init__(self, executable: str | None = None):
        self._executable = executable
        self._proc: asyncio.subprocess.Process | None = None
        self._seq = 0
        self._lock = asyncio.Lock()
        self._startup_stdout = ""
        self._startup_stderr = ""

    # ---- state properties ----

    @property
    def pid(self) -> int | None:
        """Subprocess PID, or None if not started or already exited."""
        if self._proc is not None and self._proc.returncode is None:
            return self._proc.pid
        return None

    @property
    def ready(self) -> bool:
        """True if the subprocess is alive and all three pipes are open."""
        return (self._proc is not None
                and self._proc.returncode is None
                and self._proc.stdin is not None
                and self._proc.stdout is not None
                and self._proc.stderr is not None)

    @property
    def seq(self) -> int:
        """Number of eval() commands successfully sent since start()."""
        return self._seq

    # ---- executable resolution (subclass override point) ----

    def _resolve_executable(self) -> str | None:
        return find_xsdb()

    # ---- launch / banner ----

    async def _launch(self) -> None:
        exe = self._executable or self._resolve_executable()
        if exe is None:
            raise self._error_type(f"{self._tool_name} executable not found")
        if isinstance(exe, str):
            launch_cmd = _build_launch_cmd(exe, self._extra_launch_args)
        else:
            # Full command list (e.g. [python, script] in tests) used as-is.
            launch_cmd = list(exe)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *launch_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_vendor_subprocess_env(),
            )
        except (FileNotFoundError, OSError) as e:
            self._proc = None
            raise self._error_type(
                f"failed to launch {self._tool_name}: {e}") from e
        self._seq = 0
        self._startup_stdout = await self._read_banner(BANNER_READ_TIMEOUT)
        # Keep startup stderr for a later process-death diagnostic while still
        # draining it so per-eval stderr checks stay clean.  O7 R1 proved that
        # discarding this text leaves only an opaque "Connection lost" when a
        # vendor launcher exits before the first command.
        self._startup_stderr = await self._drain_stderr(STDERR_READ_TIMEOUT)

    async def _read_banner(self, timeout: float) -> str:
        if not self.ready:
            return ""
        try:
            chunk = await asyncio.wait_for(
                self._proc.stdout.read(4096), timeout=timeout)
            return chunk.decode("utf-8", errors="replace") if chunk else ""
        except asyncio.TimeoutError:
            return ""
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.debug("%s banner read failed: %s", self._tool_name, e)
            return ""

    async def _drain_stderr(self, timeout: float) -> str:
        """Read up to one stderr chunk within a bounded time. Never blocks long."""
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            chunk = await asyncio.wait_for(self._proc.stderr.read(4096),
                                           timeout=timeout)
        except asyncio.TimeoutError:
            return ""
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.debug("%s stderr read failed: %s", self._tool_name, e)
            return ""
        return chunk.decode("utf-8", errors="replace") if chunk else ""

    # ---- command execution ----

    async def eval(self, tcl: str, timeout_s: float | None = None,
                   tolerate_stderr: bool = False) -> dict:
        """Send a Tcl command and return a structured result.

        Returns a success/error dict (see module docstring). Raises
        ``self._error_type`` on eval timeout, mid-command process death, or
        a missing sentinel (parse) error.

        ``tolerate_stderr`` is for shells whose commands write routine noise
        to stderr (e.g. XSCT build output). When True the result is parsed
        by ``_parse_tolerate_stderr`` (defined on the subclass that uses it),
        which does not treat stderr as fatal.
        """
        if not self.ready:
            proc = self._proc
            extra = {
                "exit_code": proc.returncode if proc is not None else None,
            }
            return self._error_dict(
                XSDM_EVAL_ERROR,
                f"{self._tool_name} process is not ready (dead or stopped)",
                REASON_PROCESS_DEAD,
                stdout_text=self._startup_stdout,
                stderr_text=self._startup_stderr,
                extra_details=extra)
        timeout = self._default_timeout if timeout_s is None else timeout_s
        # Single channel: concurrent eval() calls serialize on the lock.
        async with self._lock:
            seq = self._seq
            marker_prefix = self._tool_name.upper()
            begin = f"__{marker_prefix}_BEGIN_{seq}__"
            end = f"__{marker_prefix}_END_{seq}__"
            full_cmd = f"puts {begin}\n{tcl}\nputs {end}\n"
            try:
                self._proc.stdin.write(full_cmd.encode("utf-8"))
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                proc = self._proc
                # Give an already-exiting vendor launcher a short chance to
                # publish its return code and stderr.  This is diagnostic only;
                # the failed channel is still killed and never reused.
                if proc is not None and proc.returncode is None:
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(proc.wait()), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                stderr_text = await self._drain_stderr(0.25)
                exit_code = proc.returncode if proc is not None else None
                await self._kill_process()
                message = f"failed to write to {self._tool_name}: {e}"
                if exit_code is not None:
                    message += f" (process exit code {exit_code})"
                return self._error_dict(
                    XSDM_EVAL_ERROR,
                    message,
                    REASON_WRITE_FAILED,
                    stdout_text=self._startup_stdout,
                    stderr_text=stderr_text or self._startup_stderr,
                    extra_details={"exit_code": exit_code})
            try:
                output = await asyncio.wait_for(
                    self._read_until(end), timeout=timeout)
            except asyncio.TimeoutError:
                await self._kill_process()
                raise self._error_type(
                    f"eval timeout after {timeout}s on {self._tool_name}")
            self._seq += 1
            stderr_text = await self._drain_stderr(STDERR_READ_TIMEOUT)
            if tolerate_stderr:
                parser = getattr(self, "_parse_tolerate_stderr", None)
                if parser is None:
                    raise self._error_type(
                        f"{self._tool_name} does not support tolerate_stderr")
                return parser(output, begin, end)
            return self._parse_output(output, begin, end, stderr_text)

    async def _read_until(self, marker: str) -> str:
        buf = bytearray()
        marker_b = marker.encode("utf-8")
        proc = self._proc
        if proc is None:
            return ""
        while marker_b not in buf:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break  # EOF: the process exited mid-command
            buf.extend(chunk)
        return buf.decode("utf-8", errors="replace")

    # ---- result parsing ----

    def _parse_output(self, output: str, begin: str, end: str,
                      stderr_text: str) -> dict:
        lines = output.splitlines()
        b_idx = -1
        e_idx = -1
        for i, line in enumerate(lines):
            if begin in line:
                b_idx = i
            elif end in line:
                e_idx = i
        if b_idx == -1 or e_idx == -1 or e_idx <= b_idx:
            raise self._error_type(
                f"parse error: sentinel markers missing in {self._tool_name} "
                "output")
        data_lines = lines[b_idx + 1:e_idx]
        # strip Tcl-shell prompt prefixes (e.g. "xsdb% ", "xsct% ") so
        # domain parsers receive clean output lines. Real XSDB can emit a
        # double prompt (``xsdb% xsdb% ...``) when an eval sends several
        # commands (e.g. "targets <id>\ntargets -target-properties"), so
        # strip every leading prompt prefix on each line. Which prefixes to
        # strip is configurable via ``_prompt_prefixes`` (Vivado uses "% ").
        prefixes = self._prompt_prefixes
        if prefixes is None:
            prefixes = (f"{self._tool_name}% ",)
        clean_lines = []
        for ln in data_lines:
            while True:
                hit = False
                for pre in prefixes:
                    if ln.startswith(pre):
                        ln = ln[len(pre):]
                        hit = True
                        break
                if not hit:
                    break
            stripped = ln.strip()
            # Drop the sentinel lines our own puts wrapper produced — both the
            # echoed command (e.g. "xsdb% puts __XSDM_BEGIN_0__") and the puts
            # output itself — so shell echo never pollutes command data.
            if stripped == begin or stripped == end:
                continue
            if stripped.startswith("puts ") and (begin in stripped
                                                 or end in stripped):
                continue
            if not self._keep_data_line(stripped):
                continue
            clean_lines.append(ln)
        data = "\n".join(clean_lines).strip()

        error_lines = [l.strip() for l in data_lines
                       if l.strip().startswith("ERROR:")]
        if error_lines:
            return self._error_dict(
                XSDM_EVAL_ERROR, "; ".join(error_lines),
                REASON_TCL_ERROR, stderr_text=stderr_text)
        if self._stderr_is_fatal and stderr_text.strip():
            return self._error_dict(
                XSDM_EVAL_ERROR, stderr_text.strip(),
                REASON_STDERR_OUTPUT)
        return {"status": "success", "data": data}

    def _keep_data_line(self, stripped: str) -> bool:
        """Hook for subclasses to drop shell noise lines from command data.

        ``stripped`` is the data line with prompt prefixes removed and leading/
        trailing whitespace trimmed. Default keeps every line. VivadoTclBridge
        overrides this to drop banner reprints (Vivado re-prints its banner
        before every command when driven through a pipe).
        """
        return True

    @staticmethod
    def _error_dict(code: str, message: str, reason_code: str,
                    stderr_text: str = "", extra_details: dict | None = None,
                    stdout_text: str = "") -> dict:
        if stdout_text and stdout_text.strip():
            message = f"{message} | startup stdout: {stdout_text.strip()[:2000]}"
        if stderr_text and stderr_text.strip():
            message = f"{message} | stderr: {stderr_text.strip()[:2000]}"
        details = {"reason_code": reason_code}
        if extra_details:
            details.update(extra_details)
        return {
            "status": "error",
            "error": {
                "code": code,
                "message": message,
                "details": details,
            },
        }

    # ---- teardown ----

    async def stop(self) -> None:
        """Terminate the subprocess. Safe to call multiple times."""
        proc = self._proc
        if proc is None:
            return
        self._proc = None  # eval() sees not-ready while we clean up
        try:
            proc.stdin.write(b"exit\n")
            try:
                await asyncio.wait_for(proc.stdin.drain(),
                                       timeout=STDIN_WRITE_GRACE)
            except asyncio.TimeoutError:
                pass
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            logger.debug("%s stop: stdin write failed: %s", self._tool_name, e)

        try:
            await asyncio.wait_for(proc.wait(), timeout=EXIT_GRACE)
            return
        except asyncio.TimeoutError:
            pass

        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE)
            except asyncio.TimeoutError:
                pass

        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE)
            except asyncio.TimeoutError:
                logger.warning("%s process did not exit after kill during stop",
                               self._tool_name)
            except ProcessLookupError:
                pass

    async def _kill_process(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE)
        except asyncio.TimeoutError:
            logger.warning("%s process did not exit after kill",
                           self._tool_name)
        except ProcessLookupError:
            pass


class XsdbBridge(_TclShellBridge):
    """Manages a persistent xsdb subprocess for JTAG debug operations.

    xsdb runs in interactive mode (no -batch). Commands are sent via stdin.
    Output is delimited by sentinel markers so command completion can be
    detected reliably even when the Tcl output is multi-line.

    ``hw_connected`` tracks whether the shell holds a hw_server connection.
    It is set by ``start(url)`` and kept in sync by ``eval()``: a successful
    ``connect`` command flips it True and a successful ``disconnect`` flips
    it False. Failed commands never change the flag (fail-closed: the shell's
    real state is then unknown, so the previous value is preserved). The
    domain layer can also manage the flag explicitly via ``set_hw_connected``.

    Lifecycle: start() -> [eval() x N] -> stop()
    """

    _tool_name = "xsdb"
    _default_timeout: float = DEFAULT_EVAL_TIMEOUT
    _error_type: type = XsdbBridgeError
    _extra_launch_args: list[str] | None = ["-interactive"]

    def __init__(self, xsdb_path: str | None = None):
        super().__init__(executable=xsdb_path)
        self._hw_connected = False

    def _resolve_executable(self) -> str | None:
        return find_xsdb()

    async def start(self, hw_server_url: str = "localhost:3121") -> None:
        """Launch xsdb. If hw_server_url is non-empty, connect to it."""
        if self.ready:
            return
        self._hw_connected = False
        await self._launch()
        if hw_server_url:
            res = await self.eval(templates.connect(hw_server_url))
            if res["status"] != "success":
                await self.stop()
                raise XsdbBridgeError(
                    f"connect to {hw_server_url} failed: "
                    f"{res['error']['message']}")
            self._hw_connected = True

    async def eval(self, tcl: str, timeout_s: float | None = None,
                   tolerate_stderr: bool = False) -> dict:
        """Send a Tcl command and keep ``hw_connected`` in sync.

        Delegates to the shared shell bridge, then syncs ``hw_connected``
        from the command: a successful ``connect`` flips it True and a
        successful ``disconnect`` flips it False. This makes manual
        ``eval("connect -url tcp:...")`` / ``eval("disconnect")`` (the
        domain layer's connect_hw_server / disconnect_hw_server path)
        update the same flag that ``start(url)`` sets.
        """
        result = await super().eval(tcl, timeout_s=timeout_s,
                                    tolerate_stderr=tolerate_stderr)
        self._sync_hw_connected(tcl, result)
        return result

    def _sync_hw_connected(self, tcl: str, result: dict) -> None:
        """Update ``_hw_connected`` from the eval command + result.

        Only a successful command is treated as evidence. A failed
        connect/disconnect leaves the flag unchanged (fail-closed: we do not
        guess the shell's real connection state from a failed command).
        """
        if result.get("status") != "success":
            return
        first = tcl.lstrip().split(None, 1)[0] if tcl.strip() else ""
        if first == "connect":
            self._hw_connected = True
        elif first == "disconnect":
            self._hw_connected = False

    def set_hw_connected(self, value: bool) -> None:
        """Explicitly set hw_connected (domain-level connection management).

        Lets the domain layer assert or clear the connection flag when it
        knows the shell's connection state by other means (e.g. recovery
        paths) without re-running a connect/disconnect command.
        """
        self._hw_connected = bool(value)

    @property
    def hw_connected(self) -> bool:
        """True if connect() succeeded on this session."""
        return self._hw_connected
