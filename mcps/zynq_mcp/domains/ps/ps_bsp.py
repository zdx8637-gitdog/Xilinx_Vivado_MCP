"""ps_bsp.py — BSP / build pipeline (11 APIs).

B06 Integration Phase, second batch. Stateless functions taking an
XsctBridge as dependency injection; each returns a ToolResponse envelope
dict built with mcps/common/tool_response.py (never a hand-written dict).

These functions drive the XSCT software-platform flow for Vitis 2023.1:
import hardware, create platform/BSP/app, add sources, set compiler
options, build, and query BSP/build/ELF status. Tcl command strings come
from adapters/xsct.templates.

Real-XSCT notes (verified against Vitis 2023.1):
  - There is no ``importhw`` / ``updatehw`` / ``bsp create`` / ``*-get-systems``.
    Hardware is imported by ``platform create -hw <xsa>``; the platform's
    software (BSP/FSBL) is materialized by ``platform generate``; the app's
    BSP is created together with the app by ``app create``.
  - Build commands write compiler noise to stderr, so they run with
    ``tolerate_stderr=True`` (the bridge wraps them in a Tcl ``catch`` and
    reports success/failure from a stdout marker).
  - ``platform list`` / ``app list`` print nothing usable, so platform and
    app discovery read the workspace filesystem (bridge.workspace).

Error model (fail-closed): every function validates its own inputs,
surfaces bridge failures via ``safe_eval`` (never a crash), and reports
command failures as structured errors mapped by ``ps_error``.
"""
from __future__ import annotations

import glob
import os
import shutil
import struct

from mcps.common.tool_response import success
from mcps.zynq_mcp.adapters.xsct import templates
from mcps.zynq_mcp.adapters.xsct.xsct_bridge import XsctBridge
from mcps.zynq_mcp.domains.ps import (
    extract_bridge_error,
    ps_error,
    safe_eval,
)

__all__ = [
    "import_hardware",
    "create_platform",
    "create_bsp",
    "update_hardware",
    "get_bsp_status",
    "create_app",
    "add_sources",
    "set_compiler_options",
    "compile_app",
    "get_build_status",
    "read_elf_info",
]

_XSA_SUFFIX = ".xsa"
_ELF_MAGIC = b"\x7fELF"
_ELF_HDR_LEN = 52

# Build commands can take a while (platform generate builds the FSBL).
_BUILD_TIMEOUT_S = 300.0

# make is a fallback for `app build` not producing an ELF (B08 R2 showed
# app build already emits the ELF in Vitis 2023.1). make.exe lives under
# <Vivado>/gnuwin/bin and is NOT on the default Windows PATH, so the fallback
# invokes it by full resolved path (see _find_make). It is typically fast
# because BSP static libraries (libxil.a) were already built by `app build`.
_MAKE_TIMEOUT_S = 60.0

# D-C: the compiler/make error detail can be very long. Cap what is returned
# in the error message but ALWAYS report the total length and the truncation
# explicitly (never silently drop output).
_MAX_BUILD_OUTPUT_LEN = 8000

# Default Vivado install root, matching vivado_bridge._DEFAULT_VIVADO_BIN's
# parent. Overridable via VIVADO_EXEC / VIVADO_ROOT (see _find_make).
_DEFAULT_VIVADO_ROOT = r"D:/Xilinx/Vivado/2023.1"

# ps_set_compiler_options — Vitis 2023.1 XSCT does not support ``app config``
# for compiler/linker flags. The only portable path is ``-D`` defines passed
# at build time via ``app build -defines`` (per legacy reference scripts).
# All other options (flags, append_args, linker_flags, include_path) are
# UNSUPPORTED in this XSCT version — DEFERRED to a future BSP version.
#
# Stored defines are keyed by workspace path so that consecutive
# set_compiler_options → compile_app calls compose correctly.
_WS_DEFINES: dict[str, str] = {}

_OPTION_FLAG = {
    "defines": "-defines",
}

_UNSUPPORTED_OPTIONS = frozenset({
    "flags", "append_args", "linker_flags", "include_path",
})


# ── small local helpers ──────────────────────────────────────────────────────
def _require_string(value, reason_code: str, name: str):
    """Return an error envelope when value is not a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        return ps_error(reason_code,
                        f"{name} must be a non-empty string, got {value!r}",
                        details={name: value})
    return None


def _require_bridge_ready(bridge):
    """Return an error envelope when the bridge is not started, else None."""
    if not getattr(bridge, "ready", False):
        return ps_error("BRIDGE_NOT_READY",
                        "bridge is not started; start the xsct process first")
    return None


def _require_workspace(bridge):
    """Return (workspace, None) or (None, error) when no workspace is known."""
    ws = getattr(bridge, "workspace", "") or ""
    if not ws:
        return None, ps_error(
            "WORKSPACE_UNKNOWN",
            "bridge has no workspace; start xsct with a workspace first")
    return ws, None


def _safe_join_path(path: str) -> str:
    """Normalize a user path to forward slashes for XSCT Tcl."""
    return path.replace("\\", "/")


def _validate_xsa(xsa_path: str):
    """Validate an XSA path. Returns (normalized, None) or (None, envelope)."""
    err = _require_string(xsa_path, "INVALID_XSA_PATH", "xsa_path")
    if err:
        return None, err
    xsa = _safe_join_path(xsa_path.strip())
    if ".." in xsa.split("/"):
        return None, ps_error(
            "PATH_ESCAPE",
            f"xsa_path must not contain '..' traversal: {xsa_path!r}",
            details={"xsa_path": xsa_path})
    if not xsa.lower().endswith(_XSA_SUFFIX):
        return None, ps_error(
            "INVALID_XSA_PATH",
            f"xsa_path must end with {_XSA_SUFFIX}: {xsa_path!r}",
            details={"xsa_path": xsa_path})
    if not os.path.isfile(xsa_path):
        return None, ps_error(
            "XSA_NOT_FOUND", f"XSA file does not exist: {xsa_path}",
            details={"xsa_path": xsa_path})
    return xsa, None


def _validate_plain_name(value, reason_code: str, name: str):
    """Validate a bare project/app/platform name (no path separators)."""
    err = _require_string(value, reason_code, name)
    if err:
        return None, err
    n = value.strip()
    if "/" in n or "\\" in n or n in (".", ".."):
        return None, ps_error(
            reason_code,
            f"{name} must not be a path or '.'/'..', got {value!r}",
            details={name: value})
    return n, None


async def _setws(bridge, project_path: str, reason_code: str):
    """Run `setws <project_path>`. Returns None or an error envelope."""
    ws = _safe_join_path(project_path.strip())
    result = await safe_eval(bridge, templates.setws(ws))
    err = extract_bridge_error(result)
    if err:
        return ps_error(reason_code, f"setws failed: {err[2]}",
                        details={"project_path": project_path})
    bridge.workspace = ws
    return None


def _list_subdirs(root: str) -> list[str]:
    """Top-level subdirectory names under root (best-effort, sorted)."""
    try:
        return sorted(e.name for e in os.scandir(root) if e.is_dir())
    except OSError:
        return []


def _discover_platform_dir(project_path: str) -> str | None:
    """Return the platform directory name in a workspace.

    A platform directory contains a ``hw`` subfolder (the app/system dirs
    do not). Returns None when no platform is found.
    """
    for name in _list_subdirs(project_path):
        if os.path.isdir(os.path.join(project_path, name, "hw")):
            return name
    return None


def _discover_app_dir(workspace: str) -> str | None:
    """Return the first app directory name in a workspace.

    An app directory contains a ``src`` subfolder (the platform and
    *_system dirs do not).
    """
    for name in _list_subdirs(workspace):
        if os.path.isdir(os.path.join(workspace, name, "src")):
            return name
    return None


def _find_elf(app_dir: str) -> str | None:
    """Return the first built ELF under an app dir (Debug/.debug)."""
    for pattern in ("Debug", ".debug", "*"):
        hits = glob.glob(os.path.join(app_dir, pattern, "*.elf"))
        if hits:
            return os.path.normpath(hits[0])
    return None


def _cap_build_output(text: str) -> str:
    """Cap build/compiler output for an error message, marking truncation.

    D-C: the FULL make/compiler output must reach the caller. When it exceeds
    ``_MAX_BUILD_OUTPUT_LEN`` the retained prefix keeps the head and an explicit
    ``...TRUNCATED: <kept>/<total>...`` marker so output is never silently
    dropped. The total length is always recoverable from the marker.
    """
    text = text or ""
    if len(text) <= _MAX_BUILD_OUTPUT_LEN:
        return text
    return (text[:_MAX_BUILD_OUTPUT_LEN] +
            f"\n...TRUNCATED: {_MAX_BUILD_OUTPUT_LEN}/{len(text)}...")


def _find_make() -> str | None:
    """Resolve make.exe under a Vivado install (``gnuwin/bin``).

    make.exe is not on the default Windows PATH; it ships with Vivado at
    ``<Vivado>/gnuwin/bin/make.exe``. Search order mirrors
    ``vivado_bridge.find_vivado``:

      1. the install root implied by a ``VIVADO_EXEC`` full path
         (``.../Vivado/<ver>/bin/vivado.exe`` → root is two levels up)
      2. ``$VIVADO_ROOT``
      3. the default install root ``D:/Xilinx/Vivado/2023.1``
      4. ``shutil.which("make")`` on PATH

    Returns the full path to make.exe, or None when not found. Never raises.
    """
    roots: list[str] = []
    val = os.environ.get("VIVADO_EXEC", "").strip()
    if val:
        roots.append(os.path.dirname(os.path.dirname(val)))
    root = os.environ.get("VIVADO_ROOT", "").strip()
    if root:
        roots.append(root)
    roots.append(_DEFAULT_VIVADO_ROOT)
    for base in roots:
        exe = os.path.join(base, "gnuwin", "bin", "make.exe")
        if os.path.isfile(exe):
            return os.path.normpath(exe)
    found = shutil.which("make")
    return os.path.normpath(found) if found else None


# ── import / platform / BSP ──────────────────────────────────────────────────
async def import_hardware(
    bridge: XsctBridge,
    xsa_path: str,
    project_path: str,
) -> dict:
    """Import a hardware definition (.xsa) into the XSCT workspace.

    Runs ``setws <project_path>`` and copies the XSA into the workspace so
    ``create_platform`` can reference it (Vitis 2023.1 has no `importhw`;
    hardware is imported when the platform is created).

    Errors: INVALID_XSA_PATH, PATH_ESCAPE, XSA_NOT_FOUND,
    INVALID_PROJECT_PATH, BRIDGE_NOT_READY, IMPORT_HW_FAILED.
    """
    xsa, verr = _validate_xsa(xsa_path)
    if verr:
        return verr
    err = _require_string(project_path, "INVALID_PROJECT_PATH", "project_path")
    if err:
        return err
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    pp = project_path.strip()
    err = await _setws(bridge, pp, "IMPORT_HW_FAILED")
    if err:
        return err
    dst = os.path.join(pp, os.path.basename(xsa_path))
    try:
        # The platform flow may already publish the XSA directly into the
        # requested XSCT workspace.  Treat that case as an idempotent import
        # instead of surfacing shutil.SameFileError and forcing callers to
        # stage the binary through an out-of-band filesystem command.
        same_file = os.path.exists(dst) and os.path.samefile(xsa_path, dst)
        if not same_file:
            shutil.copyfile(xsa_path, dst)
    except OSError as e:
        return ps_error(
            "IMPORT_HW_FAILED",
            f"failed to copy XSA into workspace: {e}",
            details={"xsa_path": xsa_path, "project_path": pp})
    return success(data={"xsa_path": xsa_path,
                         "workspace_xsa": _safe_join_path(dst),
                         "project_path": pp,
                         "imported": True,
                         "copied": not same_file}).to_dict()


async def create_platform(
    bridge: XsctBridge,
    name: str,
    project_path: str,
) -> dict:
    """Create a software platform in the XSCT workspace.

    The XSA is discovered from the workspace (copied by ``import_hardware``).
    Runs ``setws``, ``platform create -name <name> -hw <xsa>``,
    ``platform active <name>`` and ``platform write``.

    Errors: INVALID_NAME, INVALID_PROJECT_PATH, BRIDGE_NOT_READY,
    XSA_NOT_FOUND, PLATFORM_CREATE_FAILED.
    """
    n, nerr = _validate_plain_name(name, "INVALID_NAME", "name")
    if nerr:
        return nerr
    err = _require_string(project_path, "INVALID_PROJECT_PATH", "project_path")
    if err:
        return err
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    pp = project_path.strip()
    err = await _setws(bridge, pp, "PLATFORM_CREATE_FAILED")
    if err:
        return err
    xsas = glob.glob(os.path.join(pp, f"*{_XSA_SUFFIX}"))
    if not xsas:
        return ps_error(
            "XSA_NOT_FOUND",
            f"no .xsa found in the workspace {pp!r}; run ps_import_hardware "
            "first", details={"project_path": pp})
    xsa = _safe_join_path(sorted(xsas)[-1])

    result = await safe_eval(bridge, templates.platform_create(n, xsa),
                             timeout_s=_BUILD_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("PLATFORM_CREATE_FAILED",
                        f"platform create failed: {verr[2]}",
                        details={"name": n, "xsa": xsa})
    result = await safe_eval(bridge, templates.platform_activate(n),
                             timeout_s=_BUILD_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("PLATFORM_CREATE_FAILED",
                        f"platform active failed: {verr[2]}",
                        details={"name": n})
    result = await safe_eval(bridge, templates.platform_write(),
                             timeout_s=_BUILD_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("PLATFORM_CREATE_FAILED",
                        f"platform write failed: {verr[2]}",
                        details={"name": n})
    return success(data={"name": n, "project_path": pp, "xsa": xsa,
                         "cpu": "ps7_cortexa9_0", "os": "standalone",
                         "created": True}).to_dict()


async def create_bsp(
    bridge: XsctBridge,
    platform_name: str,
    project_path: str,
) -> dict:
    """Create/generate the BSP for a platform in the XSCT workspace.

    Vitis 2023.1 has no ``bsp create``; the platform's software (BSP/FSBL)
    is materialized by ``platform generate``. Runs ``setws``,
    ``platform active <platform_name>`` and ``platform generate``.

    Errors: INVALID_PLATFORM_NAME, INVALID_PROJECT_PATH, BRIDGE_NOT_READY,
    BSP_CREATE_FAILED.
    """
    pn, perr = _validate_plain_name(
        platform_name, "INVALID_PLATFORM_NAME", "platform_name")
    if perr:
        return perr
    err = _require_string(project_path, "INVALID_PROJECT_PATH", "project_path")
    if err:
        return err
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    pp = project_path.strip()
    err = await _setws(bridge, pp, "BSP_CREATE_FAILED")
    if err:
        return err
    result = await safe_eval(bridge, templates.platform_activate(pn),
                             timeout_s=_BUILD_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("BSP_CREATE_FAILED",
                        f"platform active failed: {verr[2]}",
                        details={"platform": pn})
    result = await safe_eval(bridge, templates.platform_generate(),
                             timeout_s=_BUILD_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("BSP_CREATE_FAILED",
                        f"platform generate (BSP) failed: {verr[2]}",
                        details={"platform": pn})
    return success(data={"platform": pn, "name": "bsp",
                         "generated": True}).to_dict()


async def update_hardware(bridge: XsctBridge, xsa_path: str) -> dict:
    """Update the active platform's hardware specification.

    Vitis 2023.1 has no ``updatehw``; the platform's hardware reference is
    updated with ``platform config -updatehw``.

    Errors: INVALID_XSA_PATH, PATH_ESCAPE, XSA_NOT_FOUND, BRIDGE_NOT_READY,
    UPDATE_HW_FAILED.
    """
    xsa, verr = _validate_xsa(xsa_path)
    if verr:
        return verr
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    result = await safe_eval(bridge, templates.platform_config_updatehw(xsa),
                             tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("UPDATE_HW_FAILED", f"updatehw failed: {verr[2]}",
                        details={"xsa_path": xsa_path})
    return success(data={"xsa_path": xsa_path, "updated": True}).to_dict()


async def get_bsp_status(bridge: XsctBridge) -> dict:
    """Query the BSPs present in the XSCT workspace.

    In Vitis 2023.1 each app carries a BSP (created by ``app create``), so
    the workspace's app dirs are reported as the BSP set. Reads the
    workspace filesystem via bridge.workspace (the ``*-list`` XSCT commands
    print nothing usable).

    Errors: BRIDGE_NOT_READY, WORKSPACE_UNKNOWN.
    """
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    ws, werr = _require_workspace(bridge)
    if werr:
        return werr
    names = [name for name in _list_subdirs(ws)
             if os.path.isdir(os.path.join(ws, name, "src"))]
    return success(data={"bsps": names, "count": len(names)}).to_dict()


# ── app creation / configuration / build ─────────────────────────────────────
async def create_app(
    bridge: XsctBridge,
    name: str,
    project_path: str,
) -> dict:
    """Create an application in the XSCT workspace.

    The platform is discovered from the workspace directory (dir with a
    ``hw`` subfolder). Runs ``setws`` then ``app create -name <name>`` on
    the discovered platform (template-less — ``empty_application`` is not a
    valid template name in Vitis 2023.1).

    Errors: INVALID_NAME, INVALID_PROJECT_PATH, BRIDGE_NOT_READY,
    PLATFORM_NOT_FOUND, APP_CREATE_FAILED.
    """
    n, nerr = _validate_plain_name(name, "INVALID_NAME", "name")
    if nerr:
        return nerr
    err = _require_string(project_path, "INVALID_PROJECT_PATH", "project_path")
    if err:
        return err
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    pp = project_path.strip()
    err = await _setws(bridge, pp, "APP_CREATE_FAILED")
    if err:
        return err
    platform = _discover_platform_dir(pp)
    if not platform:
        return ps_error(
            "PLATFORM_NOT_FOUND",
            "no platform found in the workspace; create one first",
            details={"project_path": pp})
    # `app create` requires an active platform; re-activate the discovered one
    # (a prior `platform generate` clears the active platform).
    result = await safe_eval(bridge, templates.platform_activate(platform),
                             tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("APP_CREATE_FAILED",
                        f"platform active failed: {verr[2]}",
                        details={"platform": platform, "name": n})
    result = await safe_eval(bridge, templates.app_create_basic(n, platform),
                             tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("APP_CREATE_FAILED", f"app create failed: {verr[2]}",
                        details={"name": n, "platform": platform})
    return success(data={"name": n, "platform": platform,
                         "created": True}).to_dict()


async def add_sources(bridge: XsctBridge, app_name: str, files: list) -> dict:
    """Add source files to a named app in the XSCT workspace (C2 fix).

    B09 black-box found the previous version guessing the target app from the
    workspace filesystem (``_discover_app_dir``) and handing the first file to
    XSCT ``importsources``, which placed sources in ``{workspace}/src/``
    instead of ``{workspace}/{app}/src/`` when the workspace contained more
    than one ``src/`` dir (a nested Vitis workspace, a ``*_system`` project,
    ...).

    Fix: the app name is an explicit parameter, and every file is copied
    deterministically on the host into ``{workspace}/{app_name}/src/``. XSCT
    is deliberately NOT used for the copy — Vitis 2023.1 ``app build``
    auto-registers ``src/`` contents into the Debug makefile (verified on a
    real workspace: a directly-copied ``main.c`` is compiled into the ELF on
    the first build and on later incremental builds). Every input file must
    exist.

    Errors: INVALID_APP_NAME, INVALID_FILES, PATH_ESCAPE, FILE_NOT_FOUND,
    BRIDGE_NOT_READY, WORKSPACE_UNKNOWN, APP_NOT_FOUND, APP_CONFIG_FAILED.
    """
    n, nerr = _validate_plain_name(app_name, "INVALID_APP_NAME", "app_name")
    if nerr:
        return nerr
    if not isinstance(files, list):
        return ps_error(
            "INVALID_FILES",
            f"files must be a list of file paths, got {type(files).__name__}")
    if not files:
        return ps_error("INVALID_FILES", "files must be a non-empty list")
    normalized = []
    seen_names: dict[str, str] = {}
    for f in files:
        if not isinstance(f, str) or not f.strip():
            return ps_error("INVALID_FILES",
                            f"each file must be a non-empty string, got {f!r}")
        if ".." in f.replace("\\", "/").split("/"):
            return ps_error("PATH_ESCAPE",
                            f"file must not contain '..' traversal: {f!r}")
        if not os.path.isfile(f):
            return ps_error("FILE_NOT_FOUND",
                            f"source file does not exist: {f}",
                            details={"file": f})
        base = os.path.basename(f)
        if base in seen_names:
            return ps_error(
                "INVALID_FILES",
                f"duplicate destination file name {base!r} for "
                f"{seen_names[base]!r} and {f!r}",
                details={"file": f, "dest_name": base})
        seen_names[base] = f
        normalized.append(_safe_join_path(f.strip()))
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    ws, werr = _require_workspace(bridge)
    if werr:
        return werr
    src_dir = os.path.join(ws, n, "src")
    if not os.path.isdir(src_dir):
        return ps_error(
            "APP_NOT_FOUND",
            f"app {n!r} has no src directory "
            f"({_safe_join_path(src_dir)!r}); create the app first",
            details={"app_name": n, "src_dir": _safe_join_path(src_dir)})
    placed = []
    for f in normalized:
        dst = os.path.join(src_dir, os.path.basename(f))
        try:
            shutil.copyfile(f, dst)
        except OSError as e:
            return ps_error(
                "APP_CONFIG_FAILED",
                f"failed to copy {f!r} into {_safe_join_path(src_dir)!r}: {e}",
                details={"file": f, "dest": _safe_join_path(dst)})
        if not os.path.isfile(dst):
            return ps_error("APP_CONFIG_FAILED",
                            f"copy did not land at {_safe_join_path(dst)!r}",
                            details={"file": f, "dest": _safe_join_path(dst)})
        placed.append(_safe_join_path(dst))
    return success(data={"app": n, "src_dir": _safe_join_path(src_dir),
                         "files": placed, "added": True}).to_dict()


async def set_compiler_options(bridge: XsctBridge, opts: dict) -> dict:
    """Set compiler options on the app in the workspace.

    `opts` maps an option key to a non-empty value. Supported keys:
    ``defines`` — ``-D`` macro defines passed at build time via
    ``app build -defines`` (the only portable option in Vitis 2023.1 XSCT).

    Keys ``flags``, ``append_args``, ``linker_flags``, and ``include_path``
    are **UNSUPPORTED** in Vitis 2023.1 XSCT — they return a clear error
    with ``reason_code=FLAG_UNSUPPORTED_IN_XSCT`` rather than silently
    failing at build time.

    Errors: INVALID_OPTIONS, INVALID_OPTION, FLAG_UNSUPPORTED_IN_XSCT,
    BRIDGE_NOT_READY, WORKSPACE_UNKNOWN, APP_NOT_FOUND.
    """
    if not isinstance(opts, dict):
        return ps_error(
            "INVALID_OPTIONS",
            f"opts must be an object mapping option keys to values, "
            f"got {type(opts).__name__}")
    if not opts:
        return ps_error("INVALID_OPTIONS", "opts must be a non-empty object")
    for key in opts:
        if key in _UNSUPPORTED_OPTIONS:
            return ps_error(
                "FLAG_UNSUPPORTED_IN_XSCT",
                f"compiler option {key!r} is not supported in Vitis 2023.1 XSCT",
                details={"unsupported_key": key,
                         "supported": sorted(_OPTION_FLAG),
                         "note": "Only -D defines are portable; pass them via 'defines' key"})
    defines = ""
    for key in sorted(opts):
        flag = _OPTION_FLAG.get(key)
        if flag is None:
            return ps_error(
                "INVALID_OPTION",
                f"unsupported compiler option {key!r}",
                details={"supported": sorted(_OPTION_FLAG)})
        value = opts[key]
        if not isinstance(value, str) or not value.strip():
            return ps_error("INVALID_OPTION",
                            f"option {key!r} must be a non-empty string, "
                            f"got {value!r}")
        if key == "defines":
            defines = value.strip()
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    ws, werr = _require_workspace(bridge)
    if werr:
        return werr
    app = _discover_app_dir(ws)
    if app is None:
        return ps_error("APP_NOT_FOUND",
                        "no app found in the XSCT workspace; create one first")
    if defines:
        _WS_DEFINES[ws] = defines
    else:
        _WS_DEFINES.pop(ws, None)
    return success(data={"app": app, "options": {"defines": defines} if defines else {},
                         "configured": True}).to_dict()


async def compile_app(bridge: XsctBridge, app_name: str) -> dict:
    """Build the app via XSCT ``app build``; ``exec make`` as a fallback.

    Vitis 2023.1 XSCT ``app build -name <app>`` already compiles and links
    the app ELF (verified by B08 R2 on a real workspace), so the manual make
    step is skipped when the ELF is present. ``exec make`` in the app's Debug
    directory is kept only as a fallback for workspaces where ``app build``
    does not emit an ELF.

    make.exe is not on the default Windows PATH (it lives under
    ``<Vivado>/gnuwin/bin``), so the fallback invokes it by full resolved
    path via ``_find_make`` (VIVADO_EXEC / VIVADO_ROOT / default install)
    rather than relying on the XSCT process's inherited PATH.

    If ``ps_set_compiler_options`` set defines for this workspace those are
    applied to the app's build configuration first (``app config -add
    define-compiler-symbols`` per symbol) so the subsequent build compiles
    with the macros (D10 fix; Vitis 2023.1 XSCT ``app build`` has no
    ``-defines`` option).

    Errors: INVALID_APP_NAME, BRIDGE_NOT_READY, BUILD_FAILED.
    """
    err = _require_string(app_name, "INVALID_APP_NAME", "app_name")
    if err:
        return err
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    ws, werr = _require_workspace(bridge)
    if werr:
        return werr
    name = app_name.strip()
    app_dir = os.path.join(ws, name)

    # Step 1 — XSCT app build (already produces the ELF in Vitis 2023.1).
    # A controlled production facade records this exact in-flight step and
    # the owned XSCT PID in the Execution Ledger.
    if hasattr(bridge, "set_current_step"):
        bridge.set_current_step("APP_BUILD")
    # D10 fix: defines configured by ps_set_compiler_options for this
    # workspace are applied to the app's build configuration BEFORE the
    # build. Vitis 2023.1 XSCT `app build` has no -defines option (verified
    # on the real tool); the supported path is `app config -name <app>
    # -add define-compiler-symbols <sym>` — one call per symbol, which
    # appends -D<sym> to the compiler options (see templates.py).
    defines = _WS_DEFINES.get(ws, "")
    if defines:
        for symbol in defines.split():
            cfg_args = templates.app_config_define_symbol(name, symbol)
            result = await safe_eval(bridge, cfg_args,
                                     timeout_s=_BUILD_TIMEOUT_S,
                                     tolerate_stderr=True)
            verr = extract_bridge_error(result)
            if verr:
                return ps_error(
                    "BUILD_FAILED",
                    f"app config define failed: {verr[2]}",
                    details={"app_name": app_name, "symbol": symbol})
    eval_args = templates.app_build(name)
    result = await safe_eval(bridge, eval_args,
                             timeout_s=_BUILD_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        return ps_error("BUILD_FAILED", f"app build failed: {verr[2]}",
                        details={"app_name": app_name})

    # Step 2 — app build emitted the ELF → done, no make needed.
    if hasattr(bridge, "observe_step"):
        await bridge.observe_step("ELF_VERIFY")
    elf = _find_elf(app_dir)
    if elf is not None:
        if hasattr(bridge, "observe_step"):
            from mcps.zynq_mcp.control.execution_ledger import OBS_COMPLETE
            await bridge.observe_step(
                "ELF_VERIFY", OBS_COMPLETE,
                vendor_status="ELF_PRESENT_AFTER_APP_BUILD")
        return success(data={"app_name": app_name, "built": True,
                             "elf": _safe_join_path(elf),
                             "build_method": "APP_BUILD"}).to_dict()

    # Step 3 — make fallback (safety net), by full make.exe path.
    # D10 note: defines were already applied to the app's build config in
    # Step 1 (`app config -add define-compiler-symbols`), which persists in
    # the app settings; XSCT regenerates the Debug makefiles (src/subdir.mk)
    # with those -D symbols baked into the compile line, so this plain
    # `exec make` builds with the same macros. The fallback never runs
    # without Step 1 having run.
    if hasattr(bridge, "set_current_step"):
        bridge.set_current_step("MAKE_FALLBACK")
    make_exe = _find_make()
    if make_exe is None:
        return ps_error(
            "BUILD_FAILED",
            f"app build produced no ELF for app {name!r} and make.exe could "
            "not be located (searched VIVADO_EXEC/VIVADO_ROOT and the "
            "default Vivado gnuwin/bin)",
            details={"app_name": app_name})
    debug_dir = _safe_join_path(os.path.join(app_dir, "Debug"))
    tcl_make = (f"cd {{{debug_dir}}}\n"
                f"exec {{{_safe_join_path(make_exe)}}}")
    result = await safe_eval(bridge, tcl_make,
                             timeout_s=_MAKE_TIMEOUT_S, tolerate_stderr=True)
    verr = extract_bridge_error(result)
    if verr:
        # D-C: return the FULL make/compiler output (capped only when very
        # long, with the truncation marker + total length), not a single line.
        raw = verr[2]
        capped = _cap_build_output(raw)
        return ps_error("BUILD_FAILED", f"make in Debug failed: {capped}",
                        details={"app_name": app_name, "debug_dir": debug_dir,
                                 "build_output_len": len(raw),
                                 "build_output_truncated": len(raw) > _MAX_BUILD_OUTPUT_LEN})

    # Verify ELF was produced after the make fallback.
    if hasattr(bridge, "observe_step"):
        await bridge.observe_step("ELF_VERIFY")
    elf = _find_elf(app_dir)
    if elf is None:
        return ps_error("BUILD_FAILED",
                        f"no ELF produced after build for app {name!r}",
                        details={"app_name": app_name})

    if hasattr(bridge, "observe_step"):
        from mcps.zynq_mcp.control.execution_ledger import OBS_COMPLETE
        await bridge.observe_step(
            "ELF_VERIFY", OBS_COMPLETE,
            vendor_status="ELF_PRESENT_AFTER_MAKE_FALLBACK")
    return success(data={"app_name": app_name, "built": True,
                         "elf": _safe_join_path(elf),
                         "build_method": "MAKE_FALLBACK"}).to_dict()


async def get_build_status(bridge: XsctBridge) -> dict:
    """Query the apps and their build status in the XSCT workspace.

    Reads the workspace filesystem via bridge.workspace: each app dir is
    reported with whether an ELF has been produced (Debug/.debug).

    Errors: BRIDGE_NOT_READY, WORKSPACE_UNKNOWN.
    """
    pre = _require_bridge_ready(bridge)
    if pre:
        return pre
    ws, werr = _require_workspace(bridge)
    if werr:
        return werr
    apps = []
    for name in _list_subdirs(ws):
        app_dir = os.path.join(ws, name)
        if not os.path.isdir(os.path.join(app_dir, "src")):
            continue
        elf = _find_elf(app_dir)
        apps.append({"name": name, "built": elf is not None,
                     "elf": _safe_join_path(elf) if elf else None})
    return success(data={"apps": apps, "count": len(apps)}).to_dict()


# ── ELF inspection ───────────────────────────────────────────────────────────
async def read_elf_info(bridge: XsctBridge, elf_path: str) -> dict:
    """Read ELF header metadata (readelf -h equivalent).

    Parses the ELF header in pure Python so no external readelf/objdump
    executable is required. The bridge parameter is accepted for the
    uniform ps_* calling convention but is not used.

    Errors: INVALID_ELF_PATH, PATH_ESCAPE, ELF_NOT_FOUND, ELF_INVALID.
    """
    err = _require_string(elf_path, "INVALID_ELF_PATH", "elf_path")
    if err:
        return err
    if ".." in elf_path.replace("\\", "/").split("/"):
        return ps_error("PATH_ESCAPE",
                        f"elf_path must not contain '..' traversal: {elf_path!r}",
                        details={"elf_path": elf_path})
    if not os.path.isfile(elf_path):
        return ps_error("ELF_NOT_FOUND",
                        f"ELF file does not exist: {elf_path}",
                        details={"elf_path": elf_path})
    try:
        with open(elf_path, "rb") as f:
            header = f.read(_ELF_HDR_LEN)
    except OSError as e:
        return ps_error("ELF_INVALID",
                        f"ELF file is not readable: {e}",
                        details={"elf_path": elf_path})
    if len(header) < _ELF_HDR_LEN or header[:4] != _ELF_MAGIC:
        return ps_error("ELF_INVALID",
                        f"file is not a valid ELF: {elf_path}",
                        details={"elf_path": elf_path})
    info = _parse_elf_header(header, elf_path)
    return success(data=info).to_dict()


def _parse_elf_header(header: bytes, elf_path: str) -> dict:
    """Parse ELF ident + type/machine/entry from a raw ELF header."""
    e_ident = header[:16]
    cls = e_ident[4] if len(e_ident) > 4 else 0
    enc = e_ident[5] if len(e_ident) > 5 else 0
    elf_class = ("ELFCLASS32" if cls == 1 else
                 "ELFCLASS64" if cls == 2 else "unknown")
    data_encoding = ("LSB" if enc == 1 else
                     "MSB" if enc == 2 else "unknown")
    endian = "<" if enc == 1 else ">"
    try:
        if elf_class == "ELFCLASS32":
            e_type, e_machine, e_version, e_entry = struct.unpack(
                f"{endian}HHII", header[16:28])
        else:
            e_type, e_machine, e_version, e_entry = struct.unpack(
                f"{endian}HHIQ", header[16:32])
    except struct.error:
        return {"elf_path": elf_path, "magic_valid": True,
                "elf_class": elf_class, "data_encoding": data_encoding,
                "type": None, "machine": None, "entry_point": None}
    return {
        "elf_path": elf_path,
        "magic_valid": True,
        "elf_class": elf_class,
        "data_encoding": data_encoding,
        "type": e_type,
        "machine": e_machine,
        "entry_point": hex(e_entry),
    }
