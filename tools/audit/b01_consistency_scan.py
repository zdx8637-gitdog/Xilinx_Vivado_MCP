"""B01 consistency scan: verify all API references exist in tables."""
import re

flow_path = r'D:\fpgaproject\docs\development\skill\B01_standard_zynq_flow.md'
test_path = r'D:\fpgaproject\docs\development\tests\B01_gpio_acceptance_spec.md'

with open(flow_path, 'r', encoding='utf-8') as f:
    flow = f.read()
with open(test_path, 'r', encoding='utf-8') as f:
    test_content = f.read()

# Extract all API names from numbered tables (ps.X, pl.X, platform.X)
table_names = set()
for m in re.finditer(r'\|\s*\d+\s*\|\s*`(ps\.\w+)', flow):
    table_names.add(m.group(1))
for m in re.finditer(r'\|\s*\d+\s*\|\s*`(pl\.\w+)', flow):
    table_names.add(m.group(1))
for m in re.finditer(r'\|\s*\d+\s*\|\s*`(platform\.\w+)', flow):
    table_names.add(m.group(1))
for m in re.finditer(r"\|\s*\d+\s*\|\s*`(ps\.\w+)", flow):
    table_names.add(m.group(1))
for m in re.finditer(r"\|\s*\d+\s*\|\s*`(pl\.\w+)", flow):
    table_names.add(m.group(1))
for m in re.finditer(r"\|\s*\d+\s*\|\s*`(platform\.\w+)", flow):
    table_names.add(m.group(1))

ps_count = len([n for n in table_names if n.startswith('ps.')])
pl_count = len([n for n in table_names if n.startswith('pl.')])
pf_count = len([n for n in table_names if n.startswith('platform.')])

print(f"API table entries: PS={ps_count}, PL={pl_count}, Platform={pf_count}, Total={len(table_names)}")

# Check for dangerous references in test spec
danger = ['ps.reconnect_target', 'pl.add_sources']
for d in danger:
    if d in test_content:
        print(f"WARNING: {d} still referenced in test spec!")
    else:
        print(f"CLEAN: {d} not in test spec")

if 'lock_acquire(cable_serial)' in flow and 'hw_server_url' not in 'lock_acquire(cable_serial)':
    # Check if we fixed the lock key
    if re.search(r'lock_acquire\(hw_server_url,\s*cable_serial', flow):
        print("CLEAN: JTAG lock key uses hw_server_url + cable_serial")
    else:
        print("WARNING: JTAG lock key may still use old cable_serial only format")

# Check capture lifecycle APIs
for api in ['ps.start_uart_capture', 'ps.wait_uart_capture', 'ps.stop_uart_capture']:
    if api in table_names:
        print(f"CLEAN: {api} in PS API table")
    else:
        print(f"MISSING: {api} not in PS API table")

print("\nFinal API counts:")
for mcp_name, prefix in [("Platform MCP", "platform."), ("PL MCP", "pl."), ("PS MCP", "ps.")]:
    apis = [n for n in sorted(table_names) if n.startswith(prefix)]
    count = len(apis)
    print(f"  {mcp_name}: {count}")
    for a in apis:
        print(f"    {a}")
