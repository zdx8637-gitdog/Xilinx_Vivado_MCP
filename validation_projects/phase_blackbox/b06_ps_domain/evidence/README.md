# evidence/ — runner output

This directory is populated by `runner.py`. It is not checked in.

Each run creates `evidence/<run_id>/`:

| File | Meaning |
|------|---------|
| `summary.json` | Overall pass/fail, per-scenario status, skip reasons, environment probe |
| `environment.json` | The one-shot capability probe (xsct/xsdb/hw_server reachability, serial ports, XSA/source presence) |
| `<scenario>_result.json` | Per-scenario assertion results (`PASS`/`FAIL`/`SKIP`) |
| `<scenario>/` | Per-scenario evidence subdirectory (currently minimal) |

Clean up a run with:

```bash
python validation_projects\phase_blackbox\b06_ps_domain\cleanup.py --run-id <id> --execute
```

Dry-run by default; drop `--execute` to preview.
