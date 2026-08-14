"""O6 regression: process-free PS helpers never acquire an EDA backend."""

import asyncio
import struct

import pytest

from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner,
    DomainExecutionMutex,
    _PS_LOCAL_DIRECT_TOOLS,
    _PS_XSCT_TOOL_NAMES,
)
from mcps.zynq_mcp.control.execution_ledger import (
    EXECUTION_LANE_IDLE,
    OP_SUCCEEDED,
    ledger_read_shared,
    ledger_transaction,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.domains.ps import ps_bsp


BOARD = "ALINX_AX7020_v1.0"
REVISION = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


class _BackendMustNotStart:
    async def ensure_backend(self, *args, **kwargs):
        raise AssertionError("ps_read_elf_info must not start an EDA backend")


def _write_arm_elf32(path) -> None:
    ident = bytearray(16)
    ident[:4] = b"\x7fELF"
    ident[4] = 1  # ELFCLASS32
    ident[5] = 1  # little endian
    ident[6] = 1  # ELF version
    header = bytes(ident) + struct.pack("<HHII", 2, 40, 1, 0x100000)
    path.write_bytes(header + bytes(52 - len(header)))


@pytest.mark.asyncio
async def test_ps_read_elf_info_is_process_free_through_command_runner(tmp_path):
    guard = InstanceGuard(tmp_path / "runtime", "ws-o6-local")
    guard.determine_role()
    ledger_path = tmp_path / "runtime" / "execution_ledger.json"
    session_id = "session-o6-local"

    def initialize(ledger):
        ledger.instance_id = guard.instance_id
        ledger.workspace_id = guard.workspace_id
        ledger.primary_instance_id = guard.instance_id
        ledger.execution_lane = EXECUTION_LANE_IDLE
        ledger.context.update({
            "session_id": session_id,
            "board_id": BOARD,
            "project_path": str(tmp_path),
            "board_package_revision": REVISION,
            "expected_board_revision": REVISION,
        })
        return ledger

    ledger_transaction(guard, ledger_path, initialize)
    elf = tmp_path / "app.elf"
    _write_arm_elf32(elf)
    registry = OperationRegistry()
    runner = CommandRunner(
        guard,
        ledger_path,
        registry,
        DomainExecutionMutex(),
        process_controller=_BackendMustNotStart(),
    )
    try:
        response = await runner.run_command(
            tool_name="ps_read_elf_info",
            arguments={"elf_path": str(elf)},
            session_id=session_id,
            board_id=BOARD,
            project_path=str(tmp_path),
            executor="local",
            local_fn=ps_bsp.read_elf_info,
            timeout_s=5,
        )
        assert response["status"] == "success"
        operation_id = response["data"]["operation_id"]

        for _ in range(100):
            ledger, _ = ledger_read_shared(guard, ledger_path)
            previous = ledger.previous_operation or {}
            if previous.get("operation_id") == operation_id:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("ps_read_elf_info operation did not reach a terminal state")

        assert previous["status"] == OP_SUCCEEDED
        assert ledger.execution_lane == EXECUTION_LANE_IDLE
        assert ledger.worker.get("state") == "ABSENT"
        assert "ps_read_elf_info" in _PS_LOCAL_DIRECT_TOOLS
        assert "ps_read_elf_info" not in _PS_XSCT_TOOL_NAMES
    finally:
        guard.release_owner_lock()
