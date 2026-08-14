"""Child process: check if owner lock was inherited from parent."""
import sys, os
from pathlib import Path

# Add fpgaproject to path (4 levels up from helpers/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from mcps.zynq_mcp.control.instance_guard import InstanceGuard

rt = Path(sys.argv[1])
wsid = sys.argv[2] if len(sys.argv) > 2 else "ws-cp3"
g = InstanceGuard(rt, wsid)
role = g.determine_role()
print(f"CHILD_ROLE:{role.name}")
