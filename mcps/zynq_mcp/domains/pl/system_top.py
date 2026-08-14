"""
system_top.py — Platform Manifest binder + Verilog wrapper parser + system_top generator.
R3.1-B: component-internal only. No MCP tool registration, no Dispatcher integration.
"""
import copy, hashlib, json, logging, os, re
from pathlib import Path

from mcps.common.artifact_schema import _revision_to_filename, validate_manifest
from mcps.common.revision import is_sha256, sha256_file

logger = logging.getLogger("zynq_mcp.pl.system_top")

_PLATFORM_REV_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_RTL_DIR = "rtl"
_OUTPUT_FILE = "system_top.v"
_MANIFEST_SUBDIR = os.path.join("manifests", "platform")


class ManifestBindingError(ValueError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class WrapperParseError(ValueError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class PathSafetyError(ValueError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


class AtomicWriteError(OSError):
    def __init__(self, message: str, primary_error=None, cleanup_error=None):
        self.primary_error = primary_error
        self.cleanup_error = cleanup_error
        super().__init__(message)


# ── path safety ───────────────────────────────────────────────────────────────

def _is_same_file_or_contained(a, b):
    """True if `a` is equal to or a subpath of `b`. Uses realpath + normcase."""
    try:
        ra = os.path.realpath(a)
        rb = os.path.realpath(b)
        if ra == rb:
            return True
        if os.path.commonpath([rb, ra]) == rb:
            return True
    except ValueError:
        return False
    if os.name == "nt":
        if os.path.normcase(ra).startswith(os.path.normcase(rb) + os.sep):
            return True
        if os.path.normcase(ra) == os.path.normcase(rb):
            return True
    return False


def _check_directory_lexical(physical_dir, lexical_parent, label):
    """Verify `physical_dir` is within `lexical_parent` and not a symlink/junction escape.
    Returns the real path if safe. Raises ManifestBindingError or PathSafetyError.
    """
    is_manifest = "manifest" in label
    err_cls = ManifestBindingError if is_manifest else PathSafetyError
    rc = "MANIFEST_PATH_ESCAPE" if is_manifest else "PATH_ESCAPE"

    # Detect symlinks and Windows junctions/reparse points
    if os.path.islink(physical_dir):
        raise err_cls(rc, f"{label}={physical_dir} is a symlink")
    # Additional Windows junction detection: realpath resolves them
    if os.name == "nt":
        real_pd = os.path.realpath(physical_dir)
        if os.path.normcase(real_pd) != os.path.normcase(os.path.normpath(physical_dir)):
            raise err_cls(rc, f"{label}={physical_dir} is a junction (resolves to {real_pd})")

    real_phys = os.path.realpath(physical_dir)
    real_lex = os.path.realpath(lexical_parent)
    if not _is_same_file_or_contained(real_phys, real_lex):
        raise err_cls(rc, f"{label}={physical_dir} real={real_phys} outside {real_lex}")
    return real_phys


def _validate_contained(rel_path, base_dir, *, field_name="path"):
    """Validate rel_path is a safe, contained path under base_dir.
    Returns resolved absolute path.
    Raises PathSafetyError for caller paths, ManifestBindingError for manifest paths.
    """
    if not isinstance(rel_path, str) or not rel_path:
        raise PathSafetyError("INVALID_ARGUMENT", f"{field_name} must be non-empty string")

    normalized = rel_path.replace("\\", "/")
    is_manifest = "manifest" in field_name

    if os.path.isabs(normalized):
        if is_manifest:
            raise ManifestBindingError("MANIFEST_PATH_ESCAPE", f"{field_name}={rel_path}")
        raise PathSafetyError("PATH_ABSOLUTE", f"{field_name}={rel_path}")

    if len(normalized) >= 2 and normalized[1] == ":" and not os.path.isabs(normalized):
        if is_manifest:
            raise ManifestBindingError("MANIFEST_PATH_ESCAPE", f"{field_name}={rel_path}")
        raise PathSafetyError("PATH_DRIVE_RELATIVE", f"{field_name}={rel_path}")

    if normalized.startswith("//"):
        if is_manifest:
            raise ManifestBindingError("MANIFEST_PATH_ESCAPE", f"{field_name}={rel_path}")
        raise PathSafetyError("PATH_ABSOLUTE", f"{field_name}={rel_path}")

    real_base = os.path.realpath(base_dir)
    resolved = os.path.realpath(os.path.join(real_base, normalized))

    try:
        if os.path.commonpath([real_base, resolved]) != real_base:
            if is_manifest:
                raise ManifestBindingError("MANIFEST_PATH_ESCAPE", f"{field_name}={rel_path}")
            raise PathSafetyError("PATH_ESCAPE", f"{field_name}={rel_path}")
    except ValueError:
        if is_manifest:
            raise ManifestBindingError("MANIFEST_PATH_ESCAPE", f"{field_name}={rel_path} (cross-drive)")
        raise PathSafetyError("PATH_ESCAPE", f"{field_name}={rel_path} (cross-drive)")

    if os.name == "nt":
        if not os.path.normcase(resolved).startswith(os.path.normcase(real_base) + os.sep) and os.path.normcase(resolved) != os.path.normcase(real_base):
            if is_manifest:
                raise ManifestBindingError("MANIFEST_PATH_ESCAPE", f"{field_name}={rel_path}")
            raise PathSafetyError("PATH_ESCAPE", f"{field_name}={rel_path}")

    return resolved


# ── validate_manifest issue priority ──────────────────────────────────────────

_ISSUE_PRIORITY = [
    "UNSUPPORTED_SCHEMA",
    "INVALID_TYPE",
    "MISSING_FIELD",
    "BAD_REVISION",
    "INVALID_SHA256",
    "MANIFEST_TYPE_MISMATCH",
    "PATH_NOT_FOUND",
    "SHA256_MISMATCH",
    "INVALID_TIMING",
]

_CODE_TO_REASON = {
    "UNSUPPORTED_SCHEMA": "MANIFEST_SCHEMA_INVALID",
    "INVALID_TYPE": "MANIFEST_SCHEMA_INVALID",
    "MISSING_FIELD": "MANIFEST_MISSING_FIELD",
    "BAD_REVISION": "MANIFEST_REVISION_INCONSISTENT",
    "INVALID_SHA256": "MANIFEST_SHA_FORMAT_INVALID",
    "MANIFEST_TYPE_MISMATCH": "MANIFEST_TYPE_MISMATCH",
    "PATH_NOT_FOUND": "MANIFEST_FILE_MISSING",
    "SHA256_MISMATCH": "MANIFEST_SHA_MISMATCH",
    "INVALID_TIMING": "MANIFEST_TIMING_INVALID",
}


def _pick_manifest_reason(issues):
    """Select the highest-priority issue from a non-empty list.
    Deterministic: same input always produces same reason_code.
    """
    codes = {i.code for i in issues}
    for prio_code in _ISSUE_PRIORITY:
        if prio_code in codes:
            return _CODE_TO_REASON.get(prio_code, "MANIFEST_VALIDATION_FAILED")
    return "MANIFEST_VALIDATION_FAILED"


# ── Platform Manifest binder ──────────────────────────────────────────────────

def _validate_and_bind_manifest(platform_revision, project_path, board_profile_sha256):
    """Load, validate, and cross-reference the Platform Manifest.
    Returns (manifest dict, bd_wrapper_abs, bd_wrapper_sha).
    Raises ManifestBindingError on any failure.
    """
    if not isinstance(platform_revision, str) or not _PLATFORM_REV_RE.match(platform_revision):
        raise ManifestBindingError("INVALID_PLATFORM_REVISION",
            f"platform_revision={platform_revision!r}")

    # Construct lexical base and verify it's not a junction/symlink escape
    lexical_mp = os.path.join(project_path, _MANIFEST_SUBDIR)
    _check_directory_lexical(lexical_mp, os.path.join(project_path, "manifests"),
                             "manifests.platform_dir")

    filename = _revision_to_filename(platform_revision)
    manifest_rel = os.path.join(_MANIFEST_SUBDIR, filename)
    manifest_abs = _validate_contained(manifest_rel, project_path,
                                       field_name="manifest_path")

    if not os.path.isfile(manifest_abs):
        raise ManifestBindingError("PLATFORM_MANIFEST_NOT_FOUND", f"{manifest_abs}")

    try:
        with open(manifest_abs, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        raise ManifestBindingError("MANIFEST_INVALID_JSON", str(e))
    if not isinstance(manifest, dict):
        raise ManifestBindingError("MANIFEST_INVALID", "manifest root is not dict")

    mtype = manifest.get("manifest_type", "")
    if mtype != "platform":
        raise ManifestBindingError("MANIFEST_TYPE_MISMATCH",
            f"expected 'platform', got {mtype!r}")

    declared_rev = manifest.get("platform_revision", "")
    if declared_rev != platform_revision:
        raise ManifestBindingError("PLATFORM_REVISION_MISMATCH",
            f"manifest={declared_rev!r}, ledger={platform_revision!r}")

    declared_bp = manifest.get("board_profile_sha256", "")
    if declared_bp != board_profile_sha256:
        raise ManifestBindingError("BOARD_PROFILE_MISMATCH",
            f"manifest={declared_bp!r}, ledger={board_profile_sha256!r}")

    bdw_path = manifest.get("bd_wrapper_path")
    if not isinstance(bdw_path, str) or not bdw_path:
        raise ManifestBindingError("MANIFEST_INCOMPLETE",
            f"bd_wrapper_path missing or empty: {bdw_path!r}")
    bdw_sha = manifest.get("bd_wrapper_sha256")
    if not isinstance(bdw_sha, str) or not is_sha256(bdw_sha):
        raise ManifestBindingError("MANIFEST_INCOMPLETE",
            f"bd_wrapper_sha256 missing or invalid: {bdw_sha!r}")

    # Manifest internal bd_wrapper_path safety → ManifestBindingError on escape
    bdw_abs = _validate_contained(bdw_path, project_path,
                                  field_name="manifest.bd_wrapper_path")

    if not os.path.isfile(bdw_abs):
        raise ManifestBindingError("BD_WRAPPER_NOT_FOUND", f"{bdw_abs}")
    disk_sha = sha256_file(bdw_abs)
    if disk_sha != bdw_sha:
        raise ManifestBindingError("BD_WRAPPER_SHA_MISMATCH",
            f"disk={disk_sha}, manifest={bdw_sha}")

    # validate_manifest on deep copy with resolved absolute paths
    temp_manifest = copy.deepcopy(manifest)
    xsa = temp_manifest.get("xsa_path")
    if isinstance(xsa, str) and xsa:
        xsa_abs = _validate_contained(xsa, project_path,
                                      field_name="manifest.xsa_path")
        temp_manifest["xsa_path"] = xsa_abs
    temp_manifest["bd_wrapper_path"] = bdw_abs

    issues = validate_manifest(temp_manifest, "platform")
    if issues:
        rc = _pick_manifest_reason(issues)
        raise ManifestBindingError(rc,
            f"validate_manifest: {', '.join(i.code for i in issues)}")

    return manifest, bdw_abs, bdw_sha


# ── Verilog wrapper parser ────────────────────────────────────────────────────

def _make_escaped_token(semantic_name):
    """Return the canonical Verilog escaped token: \\name<space>."""
    return "\\" + semantic_name + " "


def _is_plain_identifier(name):
    """True if name is a simple Verilog identifier (letters, digits, underscore)."""
    return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name))


def _parse_wrapper(file_path):
    """Parse a Vivado 2023.1 BD wrapper and return (module_name, ports).
    Each port: {semantic_name, emitted_token, escaped, direction, width, msb, lsb}.
    emitted_token for escaped ports: always "\\semantic_name ".
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception as e:
        raise WrapperParseError("FILE_READ_ERROR", str(e))

    raw = _strip_preprocessor(raw)
    if not raw.strip():
        raise WrapperParseError("EMPTY_FILE", "file contains no Verilog source")

    stripped = _strip_comments(raw)

    mod_match = re.search(r'\bmodule\s+(\w+)\s*', stripped)
    if not mod_match:
        raise WrapperParseError("NO_MODULE", "no 'module' keyword found")
    module_name = mod_match.group(1)

    mod_matches = list(re.finditer(r'\bmodule\s+\w+', stripped))
    if len(mod_matches) > 1:
        raise WrapperParseError("MULTIPLE_MODULES",
            f"found {len(mod_matches)} module declarations")

    if not re.search(r'\bendmodule\b', stripped):
        raise WrapperParseError("UNCLOSED_MODULE", "no 'endmodule' found")

    ansi_match = re.search(r'\bmodule\s+\w+\s*\(\s*(.*?)\s*\)\s*;', stripped, re.DOTALL)
    if not ansi_match:
        raise WrapperParseError("NO_PORT_LIST", "cannot locate port list")

    port_section = ansi_match.group(1).strip()
    rest = stripped[ansi_match.end():]

    if re.match(r'\s*(input|output|inout)\b', port_section):
        ports = _parse_ansi_ports(port_section)
    else:
        port_names = _parse_non_ansi_port_names(port_section)
        direction_decls = _extract_direction_decls(rest)
        if not direction_decls:
            raise WrapperParseError("NO_PORT_DIRECTIONS",
                "no input/output/inout declarations found after port list")
        ports = _build_ports_non_ansi(port_names, direction_decls)

    if not ports:
        raise WrapperParseError("NO_PORT_DIRECTIONS", "no ports parsed")

    names = [p["semantic_name"] for p in ports]
    if len(names) != len(set(names)):
        dups = sorted(set(n for n in names if names.count(n) > 1))
        raise WrapperParseError("DUPLICATE_PORT", f"duplicate: {dups}")

    return module_name, ports


def _strip_preprocessor(raw):
    return re.sub(r'`\S+.*', '', raw)


def _strip_comments(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    text = re.sub(r'//[^\n]*', ' ', text)
    return text


def _extract_escaped_tokens(text_section):
    """Walk a section and extract escaped identifier tokens in canonical form.
    Returns list of (semantic_name, canonical_token, escaped).
    Non-escaped tokens returned as (name, name, False).
    """
    results = []
    i = 0
    current = []
    while i < len(text_section):
        ch = text_section[i]
        if ch == '\\':
            j = i + 1
            while j < len(text_section) and text_section[j] not in (' ', '\t', '\n', '\r', ','):
                j += 1
            sem = text_section[i + 1:j]
            # consume terminating whitespace if present
            if j < len(text_section) and text_section[j] in (' ', '\t', '\n', '\r'):
                j += 1
            results.append((sem, _make_escaped_token(sem), True))
            i = j
            continue
        elif ch in (' ', '\t', '\n', '\r', ','):
            tok = ''.join(current).strip()
            if tok and _is_plain_identifier(tok):
                results.append((tok, tok, False))
            elif tok:
                # Unrecognized token: escaped without explicit backslash
                results.append((tok, _make_escaped_token(tok), True))
            current = []
            i += 1
            continue
        else:
            current.append(ch)
            i += 1
    tok = ''.join(current).strip()
    if tok and _is_plain_identifier(tok):
        results.append((tok, tok, False))
    elif tok:
        results.append((tok, _make_escaped_token(tok), True))
    return results


def _parse_non_ansi_port_names(port_section):
    return _extract_escaped_tokens(port_section)


def _extract_direction_decls(text):
    """Extract direction declarations from non-ANSI wrapper. Stops at wire/assign."""
    decls = []
    for stmt in text.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        if re.match(r'\b(wire|tri|assign|always|initial|if|case|generate|endgenerate)\b', stmt):
            break
        m = re.match(r'''
            \s*(input|output|inout)\s+
            (?:\[(\d+)\s*:\s*(\d+)\]\s*)?
            (.+)
            \s*$''', stmt, re.VERBOSE)
        if m:
            direction = m.group(1)
            msb_raw = m.group(2)
            lsb_raw = m.group(3)
            name_raw = m.group(4).strip()
            # Extract name from raw token
            tokens = _extract_escaped_tokens(name_raw)
            if not tokens:
                continue
            sem_name, emit_tok, escaped = tokens[0]
            decls.append({
                "semantic_name": sem_name,
                "emitted_token": emit_tok,
                "escaped": escaped,
                "direction": direction,
                "msb": int(msb_raw) if msb_raw is not None else None,
                "lsb": int(lsb_raw) if lsb_raw is not None else None,
            })
    return decls


def _build_ports_non_ansi(port_name_tuples, direction_decls):
    decl_map = {d["semantic_name"]: d for d in direction_decls}
    ports = []
    for sem_name, emit_token, escaped in port_name_tuples:
        if sem_name not in decl_map:
            raise WrapperParseError("UNDECLARED_PORT",
                f"port '{sem_name}' has no matching direction declaration")
        d = decl_map[sem_name]
        ports.append({
            "semantic_name": sem_name,
            "emitted_token": d["emitted_token"],
            "escaped": d["escaped"],
            "direction": d["direction"],
            "width": f"[{d['msb']}:{d['lsb']}]" if d["msb"] is not None else None,
            "msb": d["msb"],
            "lsb": d["lsb"],
        })
    return ports


def _parse_ansi_ports(port_section):
    """Parse ANSI port list. escaped identifiers use canonical token form."""
    ports = []
    i = 0
    chunks = []
    current = []
    while i < len(port_section):
        ch = port_section[i]
        if ch == '\\':
            j = i + 1
            while j < len(port_section) and port_section[j] not in (' ', '\t', '\n', '\r', ','):
                j += 1
            if j < len(port_section) and port_section[j] in (' ', '\t', '\n', '\r'):
                current.append(port_section[i:j + 1])
                i = j + 1
            else:
                current.append(port_section[i:j])
                i = j
            continue
        elif ch == ',':
            chunks.append(''.join(current).strip())
            current = []
            i += 1
        else:
            current.append(ch)
            i += 1
    chunks.append(''.join(current).strip())

    for tok in chunks:
        if not tok:
            continue
        m = re.match(r'\s*(input|output|inout)\s+(?:\[(\d+)\s*:\s*(\d+)\]\s*)?(.+)$', tok)
        if not m:
            raise WrapperParseError("MALFORMED_PORTS", f"cannot parse ANSI port: {tok!r}")
        direction = m.group(1)
        msb = m.group(2)
        lsb = m.group(3)
        name_raw = m.group(4).strip()
        tokens = _extract_escaped_tokens(name_raw)
        if not tokens:
            raise WrapperParseError("MALFORMED_PORTS", f"no identifier in: {tok!r}")
        sem_name, emit_tok, escaped = tokens[0]
        ports.append({
            "semantic_name": sem_name,
            "emitted_token": emit_tok,
            "escaped": escaped,
            "direction": direction,
            "width": f"[{msb}:{lsb}]" if msb is not None else None,
            "msb": int(msb) if msb is not None else None,
            "lsb": int(lsb) if lsb is not None else None,
        })
    return ports


# ── file output ───────────────────────────────────────────────────────────────

def _atomic_write_text(output_path, content):
    """Write content atomically: temp file → flush → fsync → os.replace.
    Raises AtomicWriteError if the write fails, with chained primary and cleanup errors.
    """
    tmp_path = output_path + ".tmp." + hashlib.sha256(os.urandom(8)).hexdigest()[:12]
    primary_err = None
    cleanup_err = None
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, output_path)
        return True
    except Exception as e:
        primary_err = e
        raise AtomicWriteError(str(primary_err), primary_error=primary_err) from primary_err
    finally:
        if primary_err is not None:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception as e2:
                cleanup_err = e2
                raise AtomicWriteError(
                    f"replace failed: {primary_err}; unlink also failed: {cleanup_err}",
                    primary_error=primary_err, cleanup_error=cleanup_err) from primary_err


# ── system_top generator ──────────────────────────────────────────────────────
_BANNER = """//
// system_top.v — auto-generated by zynq_mcp pl_generate_system_top
// DO NOT EDIT
//
"""
_TOP_MODULE = "system_top"


def generate_system_top(wrapper_path, project_path, platform_revision, board_profile_sha256):
    """Generate system_top.v instantiating the BD wrapper module.

    Returns dict with: output_path, system_top_sha256, wrapper_module,
    instance_name, port_count, ports, output.
    Writes {project_path}/rtl/system_top.v atomically.
    Raises: ManifestBindingError, WrapperParseError, PathSafetyError, AtomicWriteError.
    """
    # 1. Bind manifest
    manifest, bdw_abs_from_manifest, bdw_sha = _validate_and_bind_manifest(
        platform_revision, project_path, board_profile_sha256)

    # 2. Validate caller wrapper_path
    wrapper_abs = _validate_contained(wrapper_path, project_path,
                                      field_name="wrapper_path")

    # 3. Cross-check
    if os.path.normcase(wrapper_abs) != os.path.normcase(bdw_abs_from_manifest):
        raise ManifestBindingError("BD_WRAPPER_PATH_MISMATCH",
            f"caller={wrapper_abs}, manifest={bdw_abs_from_manifest}")

    disk_sha = sha256_file(wrapper_abs)
    if disk_sha != bdw_sha:
        raise ManifestBindingError("BD_WRAPPER_SHA_MISMATCH",
            f"disk={disk_sha}, manifest={bdw_sha}")

    # 4. Parse wrapper
    wrapper_module, ports = _parse_wrapper(wrapper_abs)

    # 5. Validate output directory containment (lexical base = {project_path}/rtl)
    rtl_lexical = os.path.join(project_path, _RTL_DIR)
    _check_directory_lexical(rtl_lexical, project_path, "rtl_dir")
    os.makedirs(rtl_lexical, exist_ok=True)

    output_path = os.path.join(rtl_lexical, _OUTPUT_FILE)
    real_out = os.path.realpath(output_path)
    real_proj = os.path.realpath(project_path)
    if not _is_same_file_or_contained(real_out, real_proj):
        raise PathSafetyError("PATH_ESCAPE", f"output={output_path} real={real_out}")

    # 6. Generate Verilog
    instance_name = f"{wrapper_module}_i"
    output = _BANNER
    port_tokens = [p["emitted_token"] for p in ports]
    output += f"module {_TOP_MODULE}\n  ("
    output += ",\n   ".join(port_tokens)
    output += "\n  );\n"

    for p in ports:
        width_str = f" {p['width']}" if p["width"] else ""
        output += f"  {p['direction']}{width_str} {p['emitted_token']};\n"

    output += "\n"
    for p in ports:
        width_str = f" {p['width']}" if p["width"] else ""
        output += f"  wire{width_str} {p['emitted_token']};\n"

    output += "\n"
    output += f"  {wrapper_module} {instance_name}\n       ("
    conns = [f".{p['emitted_token']}({p['emitted_token']})" for p in ports]
    output += ",\n        ".join(conns)
    output += "\n       );\n"
    output += "endmodule\n"

    # 7. Write atomically
    _atomic_write_text(output_path, output)
    output_sha = "sha256:" + hashlib.sha256(output.encode("utf-8")).hexdigest()

    return {
        "output_path": output_path,
        "system_top_sha256": output_sha,
        "module_name": _TOP_MODULE,
        "wrapper_module": wrapper_module,
        "instance_name": instance_name,
        "port_count": len(ports),
        "ports": ports,
        "output": output,
    }
