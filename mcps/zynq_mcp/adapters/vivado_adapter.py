"""
vivado_adapter.py — VivadoBridge + VivadoAdapter. Manages the old MCP stdio subprocess.
Does NOT write to the Execution Ledger — SingleWorkerController is the sole lifecycle owner.
"""
import asyncio, json as _json, logging, os, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from mcps.common.tool_response import ToolResponse, success, error
from mcps.zynq_mcp.control.workspace import resolve_workspace_root
from mcps.zynq_mcp.control.process_guard import is_pid_alive, get_process_identity, kill_process_tree_exact

logger = logging.getLogger("zynq_mcp.vivado_adapter")

INITIALIZE_TIMEOUT = 30.0; CALL_TOOL_TIMEOUT = 30.0; SHUTDOWN_TIMEOUT = 20.0
_sdk_pid_lock = asyncio.Lock()

ADAPTER_ABSENT = "absent"; ADAPTER_STARTING = "starting"; ADAPTER_READY = "ready"
ADAPTER_BUSY = "busy"; ADAPTER_POISONED = "poisoned"; ADAPTER_DEAD = "dead"


class BridgeError(Exception): pass
class BridgeTimeoutError(BridgeError): pass
class BridgeResponseParseError(BridgeError): pass
class BridgeCleanupError(BridgeError): pass
class AdapterNotReadyError(BridgeError): pass


def _resolve_server_path() -> Path:
    return resolve_workspace_root() / "Xilinx_Vivado_MCP" / "server.py"


def build_default_params() -> StdioServerParameters:
    sp = _resolve_server_path()
    if not sp.is_file():
        raise FileNotFoundError(f"Vivado MCP server not found: {sp}")
    return StdioServerParameters(
        command=sys.executable, args=[str(sp), "--log-level", "WARNING"],
        env=None, cwd=str(resolve_workspace_root() / "Xilinx_Vivado_MCP"))


def _parse_old_response(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        raise BridgeResponseParseError("empty response")
    try: data = _json.loads(raw_text)
    except (ValueError, TypeError) as e: raise BridgeResponseParseError(f"JSON: {e}") from e
    if not isinstance(data, dict): raise BridgeResponseParseError(f"not dict: {type(data).__name__}")
    st = data.get("status")
    if st is None: raise BridgeResponseParseError("missing status")
    if st not in ("success","error"): raise BridgeResponseParseError(f"status: {st!r}")
    if st == "error" and not data.get("error"): raise BridgeResponseParseError("no error")
    return data


def _classify_old_error(raw_error: str) -> tuple[str, str]:
    msg = raw_error.lower() if raw_error else ""
    if "initializing" in msg or "cold start" in msg: return ("ENV_ERROR","VIVADO_COLD_START")
    if "version" in msg and "mismatch" in msg: return ("ENV_ERROR","VIVADO_VERSION_MISMATCH")
    if "hw_server" in msg: return ("JTAG_ERROR","HW_SERVER_UNREACHABLE")
    if "not running" in msg or "process dead" in msg: return ("ENV_ERROR","VIVADO_PROCESS_DEAD")
    if "timeout" in msg: return ("TOOL_ERROR","VIVADO_TIMEOUT")
    if "not found" in msg:
        if "vivado" in msg or "executable" in msg: return ("ENV_ERROR","VIVADO_NOT_FOUND")
        return ("INVALID_ARGUMENT","FILE_NOT_FOUND")
    if "tcl" in msg or "syntax" in msg: return ("TOOL_ERROR","VIVADO_TCL_ERROR")
    if "synth" in msg or "synthesis" in msg: return ("PL_BUILD_ERROR","SYNTHESIS_FAILED")
    if "impl" in msg or "place" in msg or "route" in msg: return ("PL_BUILD_ERROR","IMPLEMENTATION_FAILED")
    if "timing" in msg: return ("PL_BUILD_ERROR","TIMING_NOT_MET")
    if "bitstream" in msg: return ("PL_BUILD_ERROR","BITSTREAM_FAILED")
    return ("TOOL_ERROR","VIVADO_TCL_ERROR")


def convert_to_b02_response(old_response: dict, *, context_ref=None) -> ToolResponse:
    old_status = old_response.get("status","error")
    if old_status == "success":
        resp = success(data=old_response.get("data"), context_ref=context_ref)
    else:
        raw_err = old_response.get("error","Unknown error")
        ec, rc = _classify_old_error(str(raw_err))
        resp = error(message=str(raw_err), code=ec, context_ref=context_ref)
        if resp.error is not None: resp.error.details = {"reason_code": rc}
    w = old_response.get("warnings")
    if isinstance(w, list) and w: resp.warnings = list(w)
    return resp


@dataclass
class ShutdownResult:
    cleaned: bool; error: Optional[str] = None
    cleanup_errors: list = None
    def __post_init__(self):
        if self.cleanup_errors is None: self.cleanup_errors = []
    @staticmethod
    def ok(): return ShutdownResult(cleaned=True)
    @staticmethod
    def fail(m): return ShutdownResult(cleaned=False, error=m)


# ---- VivadoBridge (queue + owner_task) ----

class VivadoBridge:
    """Single-owner task wraps MCP SDK lifecycle inside one task. No cross-task cancel-scope errors."""

    def __init__(self, *, command=None, args=None, cwd=None, env=None):
        self._command = command or sys.executable; self._args = list(args) if args else []
        self._cwd = cwd or os.getcwd(); self._env = env
        self._queue: asyncio.Queue = asyncio.Queue()
        self._owner_task: Optional[asyncio.Task] = None
        self._started = False; self._poisoned = False
        self.child_pid: Optional[int] = None

    @property
    def is_started(self): return self._started and not self._poisoned
    @property
    def is_poisoned(self): return self._poisoned

    async def start(self):
        if self._started and not self._poisoned: return
        self._queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        self._queue.put_nowait(("start", ready))
        self._owner_task = loop.create_task(self._owner_loop())
        await asyncio.wait_for(ready, timeout=INITIALIZE_TIMEOUT + 10)

    async def shutdown(self) -> ShutdownResult:
        if self._owner_task is None or self._owner_task.done():
            self._started = False; return ShutdownResult.ok()
        fut = asyncio.get_running_loop().create_future()
        try: self._queue.put_nowait(("shutdown", fut))
        except Exception: return ShutdownResult.fail("cannot signal")
        try: await asyncio.wait_for(fut, timeout=SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError: self._owner_task.cancel(); return ShutdownResult.fail("timeout")
        self._started = False
        return ShutdownResult.ok()

    async def call_tool(self, name, arguments, *, timeout=CALL_TOOL_TIMEOUT, session_id=None) -> ToolResponse:
        if not self._started or self._poisoned: raise BridgeError("not started/poisoned")
        fut = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(("call_tool",(name,arguments,timeout,session_id,fut)))
        try: return await asyncio.wait_for(fut, timeout=timeout+10.0)
        except asyncio.TimeoutError: self._poisoned=True; raise BridgeTimeoutError(f"'{name}' timed out")

    async def list_tools(self):
        if not self._started or self._poisoned: raise BridgeError("not started/poisoned")
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put(("list_tools", fut))
        return await asyncio.wait_for(fut, timeout=CALL_TOOL_TIMEOUT)

    async def _owner_loop(self):
        session = None; ctx = None; rs = ws = None
        try:
            while True:
                item = await self._queue.get()
                cmd = item[0]
                if cmd == "start":
                    fut = item[1]
                    try:
                        params = StdioServerParameters(command=self._command,args=self._args,env=self._env,cwd=self._cwd)
                        import mcp.client.stdio as mod
                        pi = {'pid': None}
                        async with _sdk_pid_lock:
                            saved = mod._create_platform_compatible_process
                            async def _p(*a,**kw): p=await saved(*a,**kw); pi['pid']=p.pid; return p
                            mod._create_platform_compatible_process = _p
                            try: ctx = stdio_client(params); rs,ws = await asyncio.wait_for(ctx.__aenter__(), INITIALIZE_TIMEOUT)
                            finally: mod._create_platform_compatible_process = saved
                        self.child_pid = pi['pid']
                        session = ClientSession(rs,ws)
                        await asyncio.wait_for(session.__aenter__(), INITIALIZE_TIMEOUT)
                        await asyncio.wait_for(session.initialize(), INITIALIZE_TIMEOUT)
                        self._started = True; self._poisoned = False; fut.set_result(True)
                    except Exception as e: self._started = False; fut.set_exception(e); return

                elif cmd == "list_tools":
                    fut = item[1]
                    try:
                        if session is None: raise BridgeError("no session")
                        r = await asyncio.wait_for(session.list_tools(), CALL_TOOL_TIMEOUT)
                        fut.set_result([{"name":t.name,"description":t.description,"inputSchema":t.inputSchema} for t in r.tools])
                    except Exception as e: self._poisoned = True; fut.set_exception(BridgeError(str(e)))

                elif cmd == "call_tool":
                    name, args, timeout, sid, fut = item[1]
                    try:
                        if session is None: raise BridgeError("no session")
                        r = await asyncio.wait_for(session.call_tool(name,args), timeout)
                        if not r.content: raise BridgeResponseParseError("0 content")
                        first = r.content[0]
                        if not hasattr(first,"text") or first.text is None: raise BridgeResponseParseError("no text")
                        if not first.text.strip(): raise BridgeResponseParseError("empty")
                        resp = convert_to_b02_response(_parse_old_response(first.text), context_ref=sid)
                        fut.set_result(resp)
                    except asyncio.TimeoutError: self._poisoned = True; fut.set_exception(BridgeTimeoutError(f"'{name}' timed out"))
                    except Exception as e: self._poisoned = True; fut.set_exception(BridgeError(str(e)))

                elif cmd == "shutdown":
                    fut = item[1]
                    cleanup_errs = []
                    if session is not None:
                        try: await asyncio.wait_for(session.__aexit__(None,None,None), SHUTDOWN_TIMEOUT)
                        except RuntimeError as e:
                            if "cancel scope" in str(e).lower() or "different task" in str(e).lower():
                                pass
                            else:
                                cleanup_errs.append(f"session_runtime:{e}")
                        except (Exception, asyncio.CancelledError) as e:
                            cleanup_errs.append(f"session:{e}")
                        finally: session = None
                    if ctx is not None:
                        try: await asyncio.wait_for(ctx.__aexit__(None,None,None), SHUTDOWN_TIMEOUT)
                        except RuntimeError as e:
                            if "cancel scope" in str(e).lower() or "different task" in str(e).lower():
                                pass
                            else:
                                cleanup_errs.append(f"ctx_runtime:{e}")
                        except (Exception, asyncio.CancelledError) as e:
                            cleanup_errs.append(f"ctx:{e}")
                        finally: ctx = None
                    self._started = False
                    if cleanup_errs:
                        logger.warning("Bridge shutdown cleanup errors: %s", cleanup_errs)
                    fut.set_result(True); return

        except asyncio.CancelledError:
            logger.debug("Bridge owner task cancelled")
        finally:
            final_errs = []
            if session is not None:
                try: await session.__aexit__(None,None,None)
                except Exception as e: final_errs.append(f"final_session:{e}")
            if ctx is not None:
                try: await ctx.__aexit__(None,None,None)
                except Exception as e: final_errs.append(f"final_ctx:{e}")
            if final_errs:
                logger.error("Bridge final cleanup errors: %s", final_errs)
            self._started = False


# ---- VivadoAdapter (NO ledger writes) ----

class VivadoAdapter:
    """Manages the old MCP subprocess. Does NOT write to the Execution Ledger."""

    def __init__(self):
        self._bridge: Optional[VivadoBridge] = None
        self._started = False; self._poisoned = False
        self.status = ADAPTER_ABSENT
        self._child_pid: Optional[int] = None
        self._server_path: str = ""; self._generation: int = 0

    @property
    def child_pid(self) -> Optional[int]: return self._child_pid
    @property
    def is_started(self) -> bool: return self._started and not self._poisoned
    @property
    def is_poisoned(self) -> bool: return self._poisoned
    @property
    def server_path(self) -> str: return self._server_path
    @property
    def generation(self) -> int: return self._generation

    @property
    def worker_identity(self) -> dict:
        ident = {"pid": self._child_pid, "process_start_time": None,
                 "executable_path": sys.executable,
                 "executable_args": [self._server_path],
                 "worker_generation": self._generation}
        if self._child_pid and is_pid_alive(self._child_pid):
            pi = get_process_identity(self._child_pid)
            if pi is not None:
                ident["process_start_time"] = pi.process_start_time
                ident["executable_path"] = pi.executable_path
        return ident

    async def start(self) -> None:
        if self._started and not self._poisoned: return
        sp = _resolve_server_path()
        if not sp.is_file(): raise FileNotFoundError(f"Server not found: {sp}")
        self._server_path = str(sp); self.status = ADAPTER_STARTING
        params = build_default_params()
        self._bridge = VivadoBridge(command=params.command, args=params.args, cwd=params.cwd, env=params.env)
        await self._bridge.start()
        self._child_pid = self._bridge.child_pid; self._generation += 1
        self._started = True; self._poisoned = False; self.status = ADAPTER_READY

    async def shutdown(self) -> ShutdownResult:
        pid = self._child_pid
        bridge_errors = []
        if self._bridge is not None:
            try:
                await self._bridge.shutdown()
            except Exception as e:
                bridge_errors.append(f"bridge_shutdown:{e}")
                logger.error("Bridge shutdown error: %s", e)
        self._started = False; self._poisoned = False; self.status = ADAPTER_ABSENT
        cleaned = True
        if pid and pid > 0 and is_pid_alive(pid):
            kill_process_tree_exact(pid); time.sleep(1.0)
            if is_pid_alive(pid):
                cleaned = False
                bridge_errors.append("pid_still_alive_after_kill")
        result = ShutdownResult(cleaned=cleaned)
        if bridge_errors:
            result.error = "; ".join(bridge_errors)
            result.cleanup_errors = bridge_errors
        return result

    async def call_tool(self, name, arguments, *, timeout=CALL_TOOL_TIMEOUT, session_id=None) -> ToolResponse:
        if not self.is_started: raise AdapterNotReadyError("not started")
        if not is_pid_alive(self._child_pid): self._poisoned = True; self.status = ADAPTER_POISONED; raise BridgeError("PID dead")
        return await self._bridge.call_tool(name, arguments, timeout=timeout, session_id=session_id)

    async def list_tools(self):
        if not self.is_started: raise AdapterNotReadyError("not started")
        return await self._bridge.list_tools()

    def poison(self): self._poisoned = True; self.status = ADAPTER_POISONED
