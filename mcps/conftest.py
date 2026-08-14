"""pytest conftest — inject B02 test fixture directory for backward compatibility
and register B03 custom markers.

Sets ZYNQ_BOARD_PROFILE_DIRS so existing B02 tests and subprocess MCP SDK
tests can find TEST_AX7020_MINIMAL without explicit search_dirs.

Production .mcp.json does NOT set this variable.
"""

import os

# Use os.path.abspath to preserve drive letter on Windows
# (Path.resolve() may strip it on some setups)
_FIXTURE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "common", "tests", "fixtures"))

# Set real env var — subprocesses (MCP SDK tests) inherit this
os.environ["ZYNQ_BOARD_PROFILE_DIRS"] = _FIXTURE_DIR


def pytest_configure(config):
    config.addinivalue_line("markers",
                            "host_live: requires real EDA tools installed")
    config.addinivalue_line("markers",
                            "device_live: requires real USB-UART connected")
