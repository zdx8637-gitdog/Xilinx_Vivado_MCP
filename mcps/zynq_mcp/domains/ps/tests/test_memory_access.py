"""test_memory_access.py — Agent C, memory_access module (4 APIs).

Unit tests use FakeXsdbBridge (shared conftest).
"""
import pytest

from mcps.zynq_mcp.domains.ps import memory_access as ma

pytestmark = pytest.mark.asyncio


# ══════════════════════════════════════════════════════════════════════
# -- reg_read --
# ══════════════════════════════════════════════════════════════════════

async def test_reg_read_success(connected_bridge):
    connected_bridge.set_response("rrd r0", "0x00000001")
    resp = await ma.reg_read(connected_bridge, "r0")
    assert resp["status"] == "success"
    assert resp["data"]["register"] == "r0"
    assert resp["data"]["value"] == "0x00000001"
    assert connected_bridge._eval_history[-1] == "rrd r0"


async def test_reg_read_aliased_sp(connected_bridge):
    connected_bridge.set_response("rrd sp", "sp : 0x00001000")
    resp = await ma.reg_read(connected_bridge, "SP")
    assert resp["status"] == "success"
    assert resp["data"]["register"] == "sp"
    assert resp["data"]["value"] == "0x00001000"


async def test_reg_read_bare_hex_normalized(connected_bridge):
    # Real XSDB prints register values as bare hex (no 0x prefix); the
    # returned value must be 0x-prefixed for the ps_reg_read contract.
    connected_bridge.set_response("rrd r0", "r0: ffffff28")
    resp = await ma.reg_read(connected_bridge, "r0")
    assert resp["status"] == "success"
    assert resp["data"]["value"] == "0xffffff28"


async def test_reg_read_invalid_register(connected_bridge):
    resp = await ma.reg_read(connected_bridge, "r16")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_REGISTER"
    assert connected_bridge._eval_history == []


async def test_reg_read_eval_failed(connected_bridge):
    connected_bridge.set_error("rrd r0", "register read refused")
    resp = await ma.reg_read(connected_bridge, "r0")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "REG_READ_FAILED"


async def test_reg_read_unparseable_fails_closed(connected_bridge):
    connected_bridge.set_response("rrd r0", "no value here")
    resp = await ma.reg_read(connected_bridge, "r0")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "REG_READ_FAILED"


async def test_reg_read_bridge_crash_is_fail_closed(connected_bridge):
    # A dead bridge (XsdbBridgeError) must surface as an error envelope,
    # never as an unhandled crash. The crash is caught during the target
    # selection probe, before the rrd is issued.
    connected_bridge.fail_eval = True
    resp = await ma.reg_read(connected_bridge, "r0")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "XSDM_BRIDGE_UNAVAILABLE"


# ══════════════════════════════════════════════════════════════════════
# -- reg_write --
# ══════════════════════════════════════════════════════════════════════

async def test_reg_write_int_success(connected_bridge):
    resp = await ma.reg_write(connected_bridge, "r0", 0x10)
    assert resp["status"] == "success"
    assert resp["data"]["value"] == "0x00000010"
    assert connected_bridge._eval_history[-1] == "rwr r0 0x00000010"


async def test_reg_write_hex_string_success(connected_bridge):
    resp = await ma.reg_write(connected_bridge, "pc", "0x00100040")
    assert resp["status"] == "success"
    assert connected_bridge._eval_history[-1] == "rwr pc 0x00100040"


async def test_reg_write_invalid_register(connected_bridge):
    resp = await ma.reg_write(connected_bridge, "r16", 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_REGISTER"


async def test_reg_write_invalid_value(connected_bridge):
    resp = await ma.reg_write(connected_bridge, "r0", "0xZZ")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_VALUE"


async def test_reg_write_eval_failed(connected_bridge):
    connected_bridge.set_error("rwr r0", "write refused")
    resp = await ma.reg_write(connected_bridge, "r0", 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "REG_WRITE_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- mem_read --
# ══════════════════════════════════════════════════════════════════════

async def test_mem_read_success(connected_bridge):
    connected_bridge.set_response(
        "mrd 0x1000 4",
        "0x00001000: 0x00000001 0x00000002\n"
        "0x00001010: 0x00000003 0x00000004")
    resp = await ma.mem_read(connected_bridge, 0x1000, length=4)
    assert resp["status"] == "success"
    assert resp["data"]["address"] == "0x1000"
    assert resp["data"]["words"] == ["0x00000001", "0x00000002",
                                     "0x00000003", "0x00000004"]
    assert connected_bridge._eval_history[-1] == "mrd 0x1000 4"


async def test_mem_read_real_xsdb_format_no_0x_prefix(connected_bridge):
    # B13-F8 修复轮#8: 真实 xsdb mrd 输出不带 0x 前缀
    # ("E000102C:   0000000A"——主代理真板抓取)；解析必须接受并规范化。
    connected_bridge.set_response(
        "mrd 0xE000102C 1",
        "E000102C:   0000000A")
    resp = await ma.mem_read(connected_bridge, 0xE000102C, length=1)
    assert resp["status"] == "success"
    assert resp["data"]["words"] == ["0x0000000A"]


async def test_mem_read_no_data_fails_closed(connected_bridge):
    # B13-F8 修复轮#8: mrd 对被阻断/未映射地址静默返回空输出——
    # 必须 fail-closed (MEM_READ_NO_DATA)，不得报 success+words=[]。
    connected_bridge.set_response("mrd 0x40000000 2", "")
    resp = await ma.mem_read(connected_bridge, 0x40000000, length=2)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "MEM_READ_NO_DATA"
    assert "ps_load_hardware" in resp["error"]["message"]


async def test_mem_read_invalid_address(connected_bridge):
    resp = await ma.mem_read(connected_bridge, "zzz")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_ADDRESS"


async def test_mem_read_invalid_length(connected_bridge):
    resp = await ma.mem_read(connected_bridge, 0x1000, length=0)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_LENGTH"


async def test_mem_read_eval_failed(connected_bridge):
    connected_bridge.set_error("mrd", "read refused")
    resp = await ma.mem_read(connected_bridge, 0x1000)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "MEM_READ_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- mem_write --
# ══════════════════════════════════════════════════════════════════════

async def test_mem_write_int_success(connected_bridge):
    resp = await ma.mem_write(connected_bridge, 0x1000, 0x1)
    assert resp["status"] == "success"
    assert resp["data"]["written"] == ["0x1"]
    assert connected_bridge._eval_history[-1] == "mwr 0x1000 {0x1}"


async def test_mem_write_list_success(connected_bridge):
    resp = await ma.mem_write(connected_bridge, "0x1000", [1, 2, 3])
    assert resp["status"] == "success"
    assert resp["data"]["written"] == ["0x1", "0x2", "0x3"]
    assert connected_bridge._eval_history[-1] == "mwr 0x1000 {0x1 0x2 0x3}"


async def test_mem_write_bytes_success(connected_bridge):
    resp = await ma.mem_write(connected_bridge, 0x1000,
                              b"\x01\x00\x00\x00\x02\x00\x00\x00")
    assert resp["status"] == "success"
    assert resp["data"]["written"] == ["0x1", "0x2"]
    assert connected_bridge._eval_history[-1] == "mwr 0x1000 {0x1 0x2}"


async def test_mem_write_invalid_address(connected_bridge):
    resp = await ma.mem_write(connected_bridge, "zzz", 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_ADDRESS"


async def test_mem_write_invalid_data(connected_bridge):
    resp = await ma.mem_write(connected_bridge, 0x1000, "nope")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_DATA"


async def test_mem_write_eval_failed(connected_bridge):
    connected_bridge.set_error("mwr", "write refused")
    resp = await ma.mem_write(connected_bridge, 0x1000, 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "MEM_WRITE_FAILED"


async def test_mem_write_not_connected(fake_bridge):
    resp = await ma.mem_write(fake_bridge, 0x1000, 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"
