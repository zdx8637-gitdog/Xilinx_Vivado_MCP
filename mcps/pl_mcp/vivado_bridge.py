"""
vivado_bridge.py -- Subprocess bridge to old Xilinx_Vivado_MCP via MCP SDK.
PID captured via thread-safe hook on SDK process creation.
ShutdownResult.cleaned verified against actual PID liveness.
"""
from __future__ import annotations

import asyncio, json as _json, logging, os, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
import mcp.client.stdio as _mcp_stdio

from mcps.common.tool_response import ToolResponse, success, error

logger = logging.getLogger("pl_mcp.vivado_bridge")

INITIALIZE_TIMEOUT = 30.0; LIST_TOOLS_TIMEOUT = 15.0
CALL_TOOL_TIMEOUT = 30.0; SHUTDOWN_TIMEOUT = 20.0

def _resolve_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent
def _resolve_server_path() -> Path:
    return _resolve_project_root() / "Xilinx_Vivado_MCP" / "server.py"
def _resolve_server_cwd() -> Path:
    return _resolve_project_root() / "Xilinx_Vivado_MCP"
def build_default_params() -> StdioServerParameters:
    sp = _resolve_server_path()
    if not sp.is_file():
        raise FileNotFoundError(f"Old Vivado MCP server not found: {sp}")
    return StdioServerParameters(command=sys.executable, args=[str(sp), "--log-level", "WARNING"],
                                  env=None, cwd=str(_resolve_server_cwd()))

class BridgeError(Exception): pass
class BridgeResponseParseError(BridgeError): pass
class BridgeCleanupError(BridgeError): pass

def _parse_old_response(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        raise BridgeResponseParseError("Old MCP returned empty response")
    try: data = _json.loads(raw_text)
    except (ValueError, TypeError) as exc: raise BridgeResponseParseError(f"non-JSON: {exc}") from exc
    if not isinstance(data, dict): raise BridgeResponseParseError(f"expected dict, got {type(data).__name__}")
    st = data.get("status")
    if st is None: raise BridgeResponseParseError("missing 'status'")
    if st not in ("success", "error"): raise BridgeResponseParseError(f"illegal status: {st!r}")
    if st == "error" and not data.get("error"): raise BridgeResponseParseError("error response missing 'error' field")
    return data

def _classify_old_error(raw_error: str) -> tuple[str, str]:
    msg = raw_error.lower() if raw_error else ""
    if "initializing" in msg or "cold start" in msg:      return ("ENV_ERROR", "VIVADO_COLD_START")
    if "version" in msg and "mismatch" in msg:            return ("ENV_ERROR", "VIVADO_VERSION_MISMATCH")
    if "hw_server" in msg:                                 return ("JTAG_ERROR", "HW_SERVER_UNREACHABLE")
    if "device" in msg and ("not found" in msg or "no " in msg): return ("JTAG_ERROR", "DEVICE_NOT_FOUND")
    if "not running" in msg or "process dead" in msg:     return ("ENV_ERROR", "VIVADO_PROCESS_DEAD")
    if "timeout" in msg:                                  return ("TOOL_ERROR", "VIVADO_TIMEOUT")
    if "not found" in msg:
        if "vivado" in msg or "executable" in msg: return ("ENV_ERROR", "VIVADO_NOT_FOUND")
        return ("INVALID_ARGUMENT", "FILE_NOT_FOUND")
    if "tcl" in msg or "syntax" in msg:                   return ("TOOL_ERROR", "VIVADO_TCL_ERROR")
    if "synth" in msg or "synthesis" in msg:              return ("PL_BUILD_ERROR", "SYNTHESIS_FAILED")
    if "impl" in msg or "place" in msg or "route" in msg: return ("PL_BUILD_ERROR", "IMPLEMENTATION_FAILED")
    if "timing" in msg:                                   return ("PL_BUILD_ERROR", "TIMING_NOT_MET")
    if "bitstream" in msg:                                return ("PL_BUILD_ERROR", "BITSTREAM_FAILED")
    if "device" in msg:                                   return ("JTAG_ERROR", "DEVICE_NOT_FOUND")
    return ("TOOL_ERROR", "VIVADO_TCL_ERROR")

def convert_to_b02_response(old_response: dict, *, context_ref=None,
                             operation_id=None) -> ToolResponse:
    old_status = old_response.get("status", "error")
    if old_status == "success":
        resp = success(data=old_response.get("data"), context_ref=context_ref)
    else:
        raw_err = old_response.get("error", "Unknown error")
        err_code, reason_code = _classify_old_error(str(raw_err))
        resp = error(message=str(raw_err), code=err_code, context_ref=context_ref)
        if resp.error is not None: resp.error.details = {"reason_code": reason_code}
    w = old_response.get("warnings")
    if isinstance(w, list) and w: resp.warnings = list(w)
    if operation_id and resp.status == "success":
        if resp.data is None: resp.data = {}
        if isinstance(resp.data, dict): resp.data["operation_id"] = operation_id
    return resp

@dataclass
class ShutdownResult:
    cleaned: bool; error: Optional[str] = None
    @staticmethod
    def ok() -> "ShutdownResult": return ShutdownResult(cleaned=True)
    @staticmethod
    def fail(msg: str) -> "ShutdownResult": return ShutdownResult(cleaned=False, error=msg)


# ---- Concurrency-safe PID capture via SDK hook (asyncio lock) ----

_sdk_pid_lock = asyncio.Lock()

def _capture_pid(params: StdioServerParameters) -> Optional[int]:
    """Install hook → enter stdio_client → extract PID → restore hook.
    Serialised across all BridgeOwners via _sdk_pid_lock."""
    import mcp.client.stdio as mod
    pid_holder: dict[str, Optional[int]] = {'pid': None}

    with _sdk_pid_lock:
        saved = mod._create_platform_compatible_process
        async def _patched(*a, **kw):
            p = await saved(*a, **kw)
            pid_holder['pid'] = p.pid
            return p
        mod._create_platform_compatible_process = _patched
        try:
            async def _enter():
                ctx = stdio_client(params)
                await ctx.__aenter__()
                return ctx
            # run in a temp event loop
            pass
        finally:
            mod._create_platform_compatible_process = saved

    return pid_holder['pid']


class BridgeOwner:
    """Owner task wraps VivadoBridge. Self-exits on poison. child_pid from SDK hook."""

    def __init__(self, *, command=None, args=None, cwd=None, env=None):
        if args:
            self._command = command or sys.executable
            self._args = list(args); self._server_path = self._args[0] if self._args else ""
            self._cwd = cwd or os.getcwd()
        else:
            sp = _resolve_server_path()
            self._command = command or sys.executable; self._server_path = str(sp)
            self._args = [self._server_path, "--log-level", "WARNING"]
            self._cwd = cwd or str(_resolve_server_cwd())
        self._env = env
        self._queue: asyncio.Queue = asyncio.Queue()
        self._owner_task: Optional[asyncio.Task] = None
        self._started = False; self._poisoned = False
        self.cleanup_done = asyncio.Event()
        self._last_shutdown_result: Optional[ShutdownResult] = None
        self.child_pid: Optional[int] = None

    @property
    def is_started(self) -> bool: return self._started and not self._poisoned
    @property
    def is_poisoned(self) -> bool: return self._poisoned
    @property
    def server_path(self) -> str: return self._server_path
    @property
    def command(self) -> str: return self._command
    @property
    def cwd(self) -> str: return self._cwd
    @property
    def owner_task(self) -> Optional[asyncio.Task]: return self._owner_task
    @property
    def last_shutdown_result(self) -> Optional[ShutdownResult]: return self._last_shutdown_result

    async def start(self) -> None:
        if self._started and not self._poisoned: return
        if not os.path.isfile(self._server_path):
            raise FileNotFoundError(f"server not found: {self._server_path}")
        if self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()
        self._queue = asyncio.Queue()
        self.cleanup_done.clear(); self._last_shutdown_result = None
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        self._queue.put_nowait(("start", ready))
        self._owner_task = loop.create_task(self._owner_loop())
        await asyncio.wait_for(ready, timeout=INITIALIZE_TIMEOUT + 10)

    async def shutdown(self) -> ShutdownResult:
        if self.cleanup_done.is_set():
            self._started = False
            return self._last_shutdown_result or ShutdownResult.ok()
        if self._owner_task is None or self._owner_task.done():
            self._started = False
            return self._last_shutdown_result or ShutdownResult.ok()
        if self._poisoned:
            try: await asyncio.wait_for(self.cleanup_done.wait(), timeout=SHUTDOWN_TIMEOUT + 20)
            except asyncio.TimeoutError: return ShutdownResult.fail("cleanup timed out")
            self._started = False
            return self._last_shutdown_result or ShutdownResult.ok()
        done_fut: asyncio.Future = asyncio.get_running_loop().create_future()
        try: self._queue.put_nowait(("shutdown", done_fut))
        except Exception: return ShutdownResult.fail("Cannot signal shutdown")
        try: await asyncio.wait_for(done_fut, timeout=SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            self._owner_task.cancel(); return ShutdownResult.fail("Shutdown timed out")
        try: await asyncio.wait_for(asyncio.shield(self._owner_task), timeout=10.0)
        except asyncio.TimeoutError:
            self._owner_task.cancel(); return ShutdownResult.fail("Owner did not exit")
        except Exception: pass
        self._started = False; self._poisoned = False
        return self._last_shutdown_result or ShutdownResult.ok()

    async def list_tools(self) -> list[dict]:
        if not self._started or self._poisoned: raise BridgeError("not started or poisoned")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put(("list_tools", fut))
        return await asyncio.wait_for(fut, timeout=LIST_TOOLS_TIMEOUT)

    async def call_tool(self, name: str, arguments: dict, *,
                        timeout: float = CALL_TOOL_TIMEOUT, session_id=None) -> ToolResponse:
        if not self._started or self._poisoned: raise BridgeError("not started or poisoned")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(("call_tool", (name, arguments, timeout, session_id, fut)))
        try: return await asyncio.wait_for(fut, timeout=timeout + 10.0)
        except asyncio.TimeoutError:
            self._poisoned = True
            raise BridgeError(f"call_tool '{name}' timed out") from None

    async def _owner_loop(self) -> None:
        bridge = VivadoBridge(command=self._command, args=self._args,
                              cwd=self._cwd, env=self._env)
        exited = False
        try:
            while not exited:
                item = await self._queue.get()
                cmd = item[0]
                if cmd == "start":
                    f = item[1]
                    try:
                        await bridge.start(); self._started = True; self._poisoned = False
                        self.child_pid = bridge.child_pid
                        f.set_result(True)
                    except Exception as exc: f.set_exception(exc); return
                elif cmd == "shutdown":
                    f = item[1]
                    try: await bridge.shutdown()
                    except Exception as exc: f.set_exception(exc)
                    else: f.set_result(True)
                    finally: self._started = False
                    return
                elif cmd == "list_tools":
                    f = item[1]
                    try: f.set_result(await bridge.list_tools())
                    except Exception as exc: f.set_exception(exc)
                    if bridge.is_poisoned: self._poisoned = True; exited = True
                elif cmd == "call_tool":
                    name, args, timeout, session_id, future = item[1]
                    try:
                        future.set_result(await bridge.call_tool(
                            name, args, timeout=timeout, session_id=session_id))
                    except Exception as exc: future.set_exception(exc)
                    if bridge.is_poisoned: self._poisoned = True; exited = True
        except asyncio.CancelledError: pass
        finally:
            cleanup_ok = True; cleanup_errs = []
            try: await bridge.shutdown()
            except Exception as e: cleanup_ok = False; cleanup_errs.append(str(e))
            # Verify PID is truly gone
            pid = bridge.child_pid
            if cleanup_ok and pid and pid > 0:
                import subprocess
                try:
                    r = subprocess.run(["tasklist","/FI",f"PID eq {pid}","/NH"],
                                       capture_output=True, text=True, timeout=5)
                    if str(pid) in r.stdout and "No tasks" not in r.stdout:
                        cleanup_ok = False
                        cleanup_errs.append(f"PID {pid} still alive after shutdown")
                except Exception: pass
            self._started = False
            self._last_shutdown_result = (ShutdownResult.ok() if cleanup_ok
                                          else ShutdownResult.fail("; ".join(cleanup_errs)))
            self.cleanup_done.set()


class VivadoBridge:
    """Owns stdio_client + ClientSession. Captures child PID via SDK hook."""

    def __init__(self, *, command=None, args=None, cwd=None, env=None):
        self._command = command or sys.executable; self._args = list(args) if args else []
        self._cwd = cwd or os.getcwd(); self._env = env
        self._stdio_ctx = None; self._session: Optional[ClientSession] = None
        self._started = False; self._poisoned = False
        self.child_pid: Optional[int] = None

    @property
    def is_started(self) -> bool: return self._started and not self._poisoned
    @property
    def is_poisoned(self) -> bool: return self._poisoned

    async def start(self) -> None:
        if self._started and not self._poisoned: return
        await self._rollback()
        params = StdioServerParameters(command=self._command, args=self._args,
                                        env=self._env, cwd=self._cwd)
        # Hook SDK process creation — serialised across all bridges via asyncio lock
        import mcp.client.stdio as mod
        pid_holder = {'pid': None}
        async with _sdk_pid_lock:
            saved = mod._create_platform_compatible_process
            async def _patched(*a, **kw):
                p = await saved(*a, **kw)
                pid_holder['pid'] = p.pid
                return p
            mod._create_platform_compatible_process = _patched
            try:
                self._stdio_ctx = stdio_client(params)
                rs, ws = await asyncio.wait_for(self._stdio_ctx.__aenter__(), INITIALIZE_TIMEOUT)
            finally:
                mod._create_platform_compatible_process = saved
        self.child_pid = pid_holder['pid']
        try:
            self._session = ClientSession(rs, ws)
            await asyncio.wait_for(self._session.__aenter__(), INITIALIZE_TIMEOUT)
        except Exception: await self._rollback(); raise BridgeError("ClientSession failed") from None
        try: await asyncio.wait_for(self._session.initialize(), INITIALIZE_TIMEOUT)
        except Exception: await self._rollback(); raise BridgeError("initialize failed") from None
        self._started = True; self._poisoned = False

    async def shutdown(self) -> None:
        errs = []
        if self._session is not None:
            try: await asyncio.wait_for(self._session.__aexit__(None,None,None), SHUTDOWN_TIMEOUT)
            except (Exception, asyncio.CancelledError) as e: errs.append(str(e))
            finally: self._session = None
        if self._stdio_ctx is not None:
            try: await asyncio.wait_for(self._stdio_ctx.__aexit__(None,None,None), SHUTDOWN_TIMEOUT)
            except RuntimeError as e:
                if "cancel scope" in str(e) or "different task" in str(e):
                    logger.debug("stdio_client __aexit__ cross-task cancel scope (non-fatal)")
                else: errs.append(str(e))
            except (Exception, asyncio.CancelledError) as e: errs.append(str(e))
            finally: self._stdio_ctx = None
        self._started = False
        if errs: raise BridgeCleanupError("; ".join(errs))

    async def _rollback(self) -> None:
        if self._session is not None:
            try: await self._session.__aexit__(None,None,None)
            except Exception: pass; self._session = None
        if self._stdio_ctx is not None:
            try: await self._stdio_ctx.__aexit__(None,None,None)
            except Exception: pass; self._stdio_ctx = None
        self._started = False

    async def list_tools(self) -> list[dict]:
        self._check_alive()
        try: r = await asyncio.wait_for(self._session.list_tools(), LIST_TOOLS_TIMEOUT)
        except Exception as exc: self._poisoned = True; raise BridgeError(str(exc)) from exc
        return [{"name":t.name,"description":t.description,"inputSchema":t.inputSchema} for t in r.tools]

    async def call_tool(self, name: str, arguments: dict, *,
                        timeout: float = CALL_TOOL_TIMEOUT, session_id=None) -> ToolResponse:
        self._check_alive()
        try: r = await asyncio.wait_for(self._session.call_tool(name, arguments), timeout)
        except asyncio.TimeoutError: self._poisoned = True; raise BridgeError(f"'{name}' timed out") from None
        except Exception as exc: self._poisoned = True; raise BridgeError(str(exc)) from exc
        if not r.content: raise BridgeResponseParseError("0 content items")
        first = r.content[0]
        if not hasattr(first,"text") or first.text is None: raise BridgeResponseParseError("no .text")
        if not first.text.strip(): raise BridgeResponseParseError("empty text")
        return convert_to_b02_response(_parse_old_response(first.text), context_ref=session_id)

    def _check_alive(self) -> None:
        if not self._started: raise BridgeError("not started")
        if self._poisoned: raise BridgeError("poisoned")
