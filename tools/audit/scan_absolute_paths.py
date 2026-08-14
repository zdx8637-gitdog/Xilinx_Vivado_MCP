"""Scan project for absolute D:/fpgaproject path references.
Writes results to docs/development/B00_dependency_scan.md
"""
import os
from datetime import datetime

ROOT = r'D:\fpgaproject'
EXCLUDE_DIRS = {'.git', '.Xil', '__pycache__', '_trash'}
EXCLUDE_FILES = {
    'B00_project_cleanup_plan.md',
    'B00_completion_report.md',
    'B00_dependency_scan.md',
    'scan_absolute_paths.py',
}
TARGET_EXTS = {'.py', '.tcl', '.bat', '.md'}

results = {}

for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
    for fname in filenames:
        ext = os.path.splitext(fname)[1].lower()
        if ext not in TARGET_EXTS:
            continue
        if fname in EXCLUDE_FILES:
            continue
        fpath = os.path.join(dirpath, fname)
        try:
            with open(fpath, 'rb') as fh:
                content = fh.read()
            if b'fpgaproject' in content:
                rel = os.path.relpath(fpath, ROOT)
                results.setdefault(ext, []).append(rel)
        except:
            pass

total = sum(len(v) for v in results.values())

# Write report
out_path = os.path.join(ROOT, 'docs', 'development', 'B00_dependency_scan.md')
with open(out_path, 'w', encoding='utf-8') as out:
    out.write('# B00 — Absolute Path Dependency Scan\n\n')
    out.write(f'> Scan date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
    out.write(f'> Scan method: Binary search for "fpgaproject" in .py/.tcl/.bat/.md source files\n')
    out.write(f'> Excluded dirs: {", ".join(sorted(EXCLUDE_DIRS))}\n')
    out.write(f'> Excluded files: B00_project_cleanup_plan.md, B00_completion_report.md, B00_dependency_scan.md\n\n')

    out.write('## Summary\n\n')
    out.write('| Extension | Count |\n')
    out.write('|-----------|-------|\n')
    for ext in sorted(results):
        out.write(f'| .{ext} | {len(results[ext])} |\n')
    out.write(f'| **Total** | **{total}** |\n\n')

    out.write('## File List\n\n')
    for ext in sorted(results):
        files = sorted(results[ext])
        out.write(f'### .{ext} ({len(files)} files)\n\n')
        for f in files:
            out.write(f'- `{f}`\n')
        out.write('\n')

    out.write('## Key Dependency Chains\n\n')
    out.write('| Referenced Path | Approx. References | Affected Area |\n')
    out.write('|----------------|---------------------|---------------|\n')
    out.write('| `D:/fpgaproject/hello_fpga/` | ~25 | PL test scripts, platform tests, PL UART build |\n')
    out.write('| `D:/fpgaproject/zynq_platforms/` | ~40 | G10/G11 Tcl, build, recover, download scripts |\n')
    out.write('| `D:/fpgaproject/Xilinx_Vivado_MCP/` | ~6 | Test entry points |\n\n')

    out.write('> B00 decision: No absolute paths corrected. Path normalization belongs to B04 (PL MCP) / B05 (Platform MCP) / B06 (PS MCP).\n')

print(f'Scanned. Total files with fpgaproject references: {total}')
print(f'Report written to: {out_path}')
