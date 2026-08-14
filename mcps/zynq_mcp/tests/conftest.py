"""conftest.py — shared fixtures."""
import os, shutil
from pathlib import Path
import pytest

@pytest.fixture
def tmp_runtime_root(tmp_path):
    """Inject temporary runtime_root."""
    rt = tmp_path / ".zynq_runtime"
    old = os.environ.get("ZYNQ_RUNTIME_ROOT")
    os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
    yield rt
    if old is not None:
        os.environ["ZYNQ_RUNTIME_ROOT"] = old
    else:
        os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
    shutil.rmtree(str(rt), ignore_errors=True)
