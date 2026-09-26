"""Fernando-owned context selection, global harness settings, and launch snapshots."""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "context_templates.json"
RUNTIME = ROOT / "data" / "agent_context"
HOME = Path.home()
_lock = threading.RLock()


def read_jsonc(path):
    """Strip comments/trailing commas without changing quoted strings or URLs."""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text()
    token = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/'
    text = re.sub(token, lambda m: m[0] if m[0].startswith('"') else " ", text)
    text = re.sub(r'("(?:\\.|[^"\\])*")|,\s*(?=[}\]])', lambda m: m[1] or "", text)
    return json.loads(text)


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def _merge(left, right):
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            _merge(left[key], value)
        else:
            left[key] = copy.deepcopy(value)
    return left


def opencode_base():
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "opencode"
    result = {}
    for directory in (config_dir, ROOT, ROOT / ".opencode"):
        for name in ("config.json", "opencode.json", "opencode.jsonc"):
            config = read_jsonc(directory / name)
            # Explicit local plugin references are relative to their original config.
            if "plugin" in config:
                config["plugin"] = [
                    str((directory / p).resolve())
                    if isinstance(p, str) and p.startswith(".")
                    else p
                    for p in config["plugin"]
                ]
            _merge(result, config)
    return result


def _initial():
    from src.services.mcp_client import BUNDLED_SERVERS

    servers = {
        name: {
            "description": info["description"],
            "kiro": {"command": info["command"], "args": info["args"]},
            "global": False,
        }
        for name, info in BUNDLED_SERVERS.items()
    }
    for path in (HOME / ".kiro/settings/mcp.json", ROOT / ".kiro/settings/mcp.json"):
        for name, config in read_jsonc(path).get("mcpServers", {}).items():
            item = servers.setdefault(name, {"description": name})
            item.update(kiro=config, global_enabled=not config.get("disabled", False))
    for name, config in opencode_base().get("mcp", {}).items():
        item = servers.setdefault(name, {"description": name})
        item["opencode"] = config
        # Respect existing OpenCode toggles when both harnesses have a definition.
        item["global_enabled"] = config.get("enabled", True)
    for item in servers.values():
        item["global"] = item.pop("global_enabled", False)
    documents = {}
    for directory in (HOME / ".kiro/steering", ROOT / ".kiro/steering"):
        for path in sorted(directory.glob("**/*.md")):
            key = hashlib.sha256(str(path).encode()).hexdigest()[:12]
            documents[key] = {"name": path.stem, "path": str(path), "global": False}
    return {"revision": 0, "documents": documents, "servers": servers, "templates": {}}


def get_config():
    with _lock:
        if not STORE.exists():
            _write(STORE, _initial())
        return read_jsonc(STORE)


def _ids(value, available, label):
    if not isinstance(value, list) or any(
        not isinstance(v, str) or v not in available for v in value
    ):
        raise ValueError(f"Invalid {label} selection")
    return list(dict.fromkeys(value))


def save_config(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a context configuration object")
    data = copy.deepcopy(data)
    for field in ("documents", "servers", "templates"):
        if not isinstance(data.get(field), dict):
            raise ValueError(f"{field} must be an object")
        for key, item in data[field].items():
            if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", key) or not isinstance(
                item, dict
            ):
                raise ValueError(f"Invalid {field} entry")
    for item in data["documents"].values():
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("Steering files need a name")
        if not isinstance(item.get("path"), str) or not item["path"].strip():
            raise ValueError("Steering files need a path")
        path = Path(item["path"]).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        item["path"] = str(path.resolve())
        if not path.is_file():
            raise ValueError(f"Steering file does not exist: {path}")
        if type(item.get("global", False)) is not bool:
            raise ValueError("Global selection must be a boolean")
    for name, item in data["servers"].items():
        if type(item.get("global", False)) is not bool:
            raise ValueError("Global selection must be a boolean")
        for backend in ("kiro", "opencode"):
            config = item.get(backend)
            if config is not None and not isinstance(config, dict):
                raise ValueError(f"Invalid {backend} configuration for {name}")
        if not item.get("kiro") and not item.get("opencode"):
            raise ValueError(f"Missing server configuration: {name}")
        # Validate that the selected definition can actually be launched.
        for backend in ("kiro", "opencode"):
            server_config(item, backend)
    for item in data["templates"].values():
        if not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("Templates need a name")
        if not isinstance(item.get("initial_prompt", ""), str):
            raise ValueError("Template initial prompt must be text")
        item["documents"] = _ids(
            item.get("documents", []), data["documents"], "steering"
        )
        item["servers"] = _ids(item.get("servers", []), data["servers"], "MCP")
    with _lock:
        current = get_config()
        if data.get("revision") != current["revision"]:
            raise ValueError(
                "Context settings changed elsewhere. Reload and try again."
            )
        data["revision"] = current["revision"] + 1
        _write(STORE, data)
        _push_to_harnesses(data)
    return data


def _push_to_harnesses(data):
    """Push Fernando's MCP server config to Kiro and OpenCode configs."""
    kiro_path = HOME / ".kiro/settings/mcp.json"
    kiro_config = read_jsonc(kiro_path)
    opencode_path = HOME / ".config/opencode/opencode.jsonc"
    opencode_config = read_jsonc(opencode_path)
    kiro_config["mcpServers"] = {
        name: server_config(item, "kiro")
        for name, item in data["servers"].items()
        if item.get("global")
    }
    opencode_config["mcp"] = {
        name: server_config(item, "opencode")
        for name, item in data["servers"].items()
        if item.get("global")
    }
    _write(kiro_path, kiro_config)
    _write(opencode_path, opencode_config)


def sync_harness_settings():
    with _lock:
        _push_to_harnesses(get_config())


def server_config(item, backend):
    for source, definition in ((key, item.get(key)) for key in ("kiro", "opencode")):
        if definition is None:
            continue
        if not isinstance(definition, dict):
            raise ValueError(f"{source} server configuration must be an object")
        if "url" in definition and (
            not isinstance(definition["url"], str)
            or not definition["url"].startswith(("http://", "https://"))
        ):
            raise ValueError("Remote MCP servers need an HTTP(S) URL")
        if "command" in definition:
            command = definition["command"]
            if source == "kiro" and (not isinstance(command, str) or not command):
                raise ValueError("Kiro MCP command must be a nonempty string")
            if source == "opencode" and (
                not isinstance(command, list)
                or not command
                or any(not isinstance(v, str) or not v for v in command)
            ):
                raise ValueError("OpenCode MCP command must be a nonempty string array")
        if source == "kiro" and "args" in definition:
            if not isinstance(definition["args"], list) or any(
                not isinstance(v, str) for v in definition["args"]
            ):
                raise ValueError("MCP arguments must be a string array")
        for key in ("env", "environment", "headers"):
            if key in definition and (
                not isinstance(definition[key], dict)
                or any(not isinstance(v, str) for v in definition[key].values())
            ):
                raise ValueError(f"MCP {key} must contain string values")
    config = copy.deepcopy(item.get(backend))
    if not config or not (config.get("command") or config.get("url")):
        other = item.get("kiro" if backend == "opencode" else "opencode", {})
        if backend == "opencode":
            if other.get("url"):
                config = {"type": "remote", "url": other["url"]}
                for key in ("headers", "oauth"):
                    if key in other:
                        config[key] = copy.deepcopy(other[key])
            else:
                config = {
                    "type": "local",
                    "command": [other.get("command", "")] + other.get("args", []),
                    "environment": copy.deepcopy(other.get("env", {})),
                }
        elif other.get("url"):
            config = {"url": other["url"]}
            for key in ("headers", "oauth"):
                if key in other:
                    config[key] = copy.deepcopy(other[key])
        else:
            command = other.get("command", [])
            config = {
                "command": command[0] if command else "",
                "args": command[1:],
                "env": copy.deepcopy(other.get("environment", {})),
            }
    if backend == "opencode":
        config.setdefault("type", "remote" if config.get("url") else "local")
        if config["type"] not in ("local", "remote"):
            raise ValueError("OpenCode MCP type must be local or remote")
        command = config.get("command")
        if not config.get("url") and (
            not isinstance(command, list)
            or not command
            or any(not isinstance(v, str) or not v for v in command)
        ):
            raise ValueError("OpenCode MCP command must be a nonempty string array")
        config["enabled"] = True
    else:
        if not config.get("url") and not isinstance(config.get("command"), str):
            raise ValueError("Kiro MCP command must be a string")
        if not config.get("url") and not config.get("command"):
            raise ValueError("MCP server needs a command or URL")
        config.pop("disabled", None)
    return config


def resolve(group_id, backend):
    from src.services import groups

    config = get_config()
    group = next((g for g in groups.list_groups() if g["id"] == group_id), None)
    if group_id and group is None:
        raise ValueError("Group does not exist")
    template_ids = (group or {}).get("template_ids", [])
    docs = [key for key, item in config["documents"].items() if item.get("global")]
    servers = [key for key, item in config["servers"].items() if item.get("global")]
    templates = []
    initial_prompts = []
    for key in template_ids:
        template = config["templates"].get(key)
        if template:
            templates.append({"id": key, "name": template["name"]})
            docs.extend(template["documents"])
            servers.extend(template["servers"])
            prompt = template.get("initial_prompt", "")
            if prompt.strip():
                initial_prompts.append(prompt)
    documents = []
    paths = set()
    for key in dict.fromkeys(docs):
        item = config["documents"][key]
        path = Path(item["path"]).expanduser().resolve()
        if path in paths:
            continue
        paths.add(path)
        content = path.read_text()
        documents.append(
            {
                "id": key,
                "name": item["name"],
                "path": str(path),
                "content": content,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    return {
        "revision": config["revision"],
        "group_id": group_id,
        "templates": templates,
        "initial_prompt": "\n\n".join(initial_prompts),
        "documents": documents,
        "servers": {
            key: server_config(config["servers"][key], backend)
            for key in dict.fromkeys(servers)
        },
    }


def clear_inherited_context(env):
    managed_opencode = False
    for key in ("KIRO_HOME", "XDG_CONFIG_HOME", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"):
        value = env.get(key)
        if value and Path(value).is_relative_to(RUNTIME):
            del env[key]
            if key != "KIRO_HOME":
                managed_opencode = True
    if managed_opencode:
        for key in (
            "OPENCODE_DISABLE_PROJECT_CONFIG",
            "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT",
        ):
            env.pop(key, None)


def prepare(session_id, snapshot, backend):
    """Return env overrides and CLI arguments; do not touch shared harness files."""
    directory = RUNTIME / session_id
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    instructions = directory / "steering.md"
    instructions.write_text(
        "\n\n".join(
            f"# {d['name']}\nSource: {d['path']}\n\n{d['content']}"
            for d in snapshot["documents"]
        )
    )
    instructions.chmod(0o600)
    if backend == "opencode":
        config = opencode_base()
        config["mcp"] = copy.deepcopy(snapshot["servers"])
        config["instructions"] = [str(instructions)]
        config["$schema"] = "https://opencode.ai/config.json"
        config_path = directory / "config/opencode/opencode.json"
        _write(config_path, config)
        return {
            "XDG_CONFIG_HOME": str(directory / "config"),
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_CONFIG_DIR": str(config_path.parent),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
        }, []
    kiro_home = directory / "kiro"
    settings = read_jsonc(HOME / ".kiro/settings/cli.json")
    settings["chat.disableInheritingDefaultResources"] = True
    _write(kiro_home / "settings/cli.json", settings)
    _write(
        kiro_home / "agents/fernando.json",
        {
            "name": "fernando",
            "description": "Fernando chat context",
            "tools": ["*"],
            "includeMcpJson": False,
            "mcpServers": snapshot["servers"],
            "resources": ["file://" + str(instructions)],
        },
    )
    # Native session history remains in its usual location for resume/fork support.
    sessions = kiro_home / "sessions"
    if not sessions.is_symlink() and not sessions.exists():
        sessions.symlink_to(HOME / ".kiro/sessions", target_is_directory=True)
    return {"KIRO_HOME": str(kiro_home)}, ["--agent", "fernando"]


def summary(snapshot):
    if snapshot is None:
        return {"managed": False}
    return {
        "managed": True,
        "revision": snapshot["revision"],
        "group_id": snapshot["group_id"],
        "templates": snapshot["templates"],
        "servers": list(snapshot["servers"]),
        "documents": [
            {k: d[k] for k in ("id", "name", "path", "sha256")}
            for d in snapshot["documents"]
        ],
    }
