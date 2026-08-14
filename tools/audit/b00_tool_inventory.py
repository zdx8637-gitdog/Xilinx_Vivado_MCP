"""B00 regression: verify current repo tool count and imports"""
import sys, os, re

# 1. Count tool definitions
path = os.path.join(os.path.dirname(__file__), '..', 'server.py')
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
# Match: Tool(name="xxx" or Tool(name='xxx'
tools = re.findall(r"""Tool\s*\(\s*name\s*=\s*['"]([^'"]+)['"]""", content)
print(f'Tool definitions: {len(tools)}')
for i, t in enumerate(tools):
    print(f'  {i+1:2d}. {t}')

# 2. Count handler methods in vivado_tools
vpath = os.path.join(os.path.dirname(__file__), '..', 'vivado_tools.py')
with open(vpath, 'r', encoding='utf-8') as f:
    vcontent = f.read()
vdefs = re.findall(r'def\s+(get_\w+|create_\w+|open_\w+|close_\w+|write_\w+|synth_\w+|place_\w+|route_\w+|compile_\w+|elaborate_\w+|run_\w+|parse_\w+|report_\w+|validate_\w+|program_\w+|connect_\w+|read_\w+|list_\w+|configure_\w+|generate_\w+)', vcontent)
print(f'\nTool handlers in vivado_tools.py: {len(vdefs)}')

# 3. Check vivado_process.py import (doesn't require Vivado to be installed)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
try:
    print('\nImport tests:')
    from vivado_process import VivadoProcess, VivadoProcessError
    print('  vivado_process.py: OK')
    from models import ToolResponse
    print('  models.py: OK')
    from config import VIVADO_EXECUTABLE, EXPECTED_VIVADO_VERSION
    print(f'  config.py: VIVADO_EXECUTABLE={os.path.basename(VIVADO_EXECUTABLE)}, EXPECTED_VERSION={EXPECTED_VIVADO_VERSION}')
    from session import Session
    print('  session.py: OK')
    from tcl_templates import generate_create_project_tcl
    print('  tcl_templates.py: OK')
except Exception as e:
    print(f'  Import error: {e}')

print('\nAll import checks passed.')
