import datetime
import json
import re
import subprocess
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=5).stdout.strip()


def accessibility(max_nodes, max_depth):
    Atspi.set_timeout(500, 500)
    desktop = Atspi.get_desktop(0)
    pending = [(desktop, None, 0)]
    nodes = []
    errors = []
    truncated = False
    deadline = time.monotonic() + 4
    while pending and len(nodes) < max_nodes and time.monotonic() < deadline:
        obj, parent, depth = pending.pop()
        try:
            states = obj.get_state_set().get_states()
            node = {"id": len(nodes), "parent": parent, "name": obj.get_name(),
                    "role": obj.get_role_name(), "states": [s.value_nick for s in states]}
            if "Component" in obj.get_interfaces():
                rect = obj.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
                node["bounds"] = {"x": rect.x, "y": rect.y, "width": rect.width, "height": rect.height}
            nodes.append(node)
            count = obj.get_child_count()
            if depth < max_depth:
                available = max_nodes - len(nodes) - len(pending)
                take = min(count, max(0, available))
                truncated |= take < count
                for index in reversed(range(take)):
                    child = obj.get_child_at_index(index)
                    if child is not None:
                        pending.append((child, node["id"], depth + 1))
            elif count:
                truncated = True
        except GLib.Error as error:
            errors.append(str(error))
    return {"nodes": nodes, "truncated": truncated or bool(pending), "errors": errors,
            "focused_controls": [node for node in nodes if "focused" in node["states"]],
            "scope": "Controls exposed by applications through AT-SPI; absent controls are not proof of absent UI."}


def observe(filename):
    command("scrot", "--pointer", "--overwrite", filename)
    captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pointer = dict(line.split("=", 1) for line in command("xdotool", "getmouselocation", "--shell").splitlines())
    active_id = re.search(r"0x[0-9a-fA-F]+", command("xprop", "-root", "_NET_ACTIVE_WINDOW")).group()
    active = {"id": active_id}
    if active_id.startswith("0x") and int(active_id, 16):
        active["properties"] = command("xprop", "-id", active_id, "_NET_WM_NAME", "WM_NAME", "WM_CLASS", "_NET_WM_PID")
    tree = accessibility(300, 12)
    return {"captured_at": captured_at, "pointer": {k.lower(): int(v) for k, v in pointer.items()},
            "active_window": active, "focused_controls": tree["focused_controls"],
            "accessibility_truncated": tree["truncated"], "accessibility_errors": tree["errors"],
            "context_timing": "Context sampled immediately after capture; not an atomic snapshot."}


if __name__ == "__main__":
    if sys.argv[1] == "screenshot":
        result = observe(sys.argv[2])
    else:
        result = accessibility(int(sys.argv[2]), int(sys.argv[3]))
    print(json.dumps(result))
