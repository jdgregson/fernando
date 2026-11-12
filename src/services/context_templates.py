"""Fernando-owned context selection, global harness settings, and launch snapshots."""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import sys

from src.services.providers import get_provider, provider_ids, providers

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
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
    if config_home.is_relative_to(RUNTIME):
        config_home = HOME / ".config"
    config_dir = config_home / "opencode"
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
    for provider in providers():
        for name, (definition, enabled) in provider.import_servers(sys.modules[__name__]).items():
            item = servers.setdefault(name, {'description': name})
            item[provider.id] = definition
            item['global_enabled'] = enabled
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
        for backend in provider_ids():
            config = item.get(backend)
            if config is not None and not isinstance(config, dict):
                raise ValueError(f"Invalid {backend} configuration for {name}")
        if not any(item.get(backend) for backend in provider_ids()):
            raise ValueError(f"Missing server configuration: {name}")
        # Validate that the selected definition can actually be launched.
        for backend in provider_ids():
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
    for provider in providers():
        path, config, key = provider.global_mcp(sys.modules[__name__])
        config[key] = {
            name: server_config(item, provider.id)
            for name, item in data['servers'].items() if item.get('global')
        }
        _write(path, config)


def sync_harness_settings():
    with _lock:
        _push_to_harnesses(get_config())


def server_config(item, backend):
    provider = get_provider(backend)
    definitions = {}
    for source in providers():
        definition = item.get(source.id)
        if definition is None:
            continue
        if not isinstance(definition, dict):
            raise ValueError(f"{source.id} server configuration must be an object")
        if "url" in definition and (
            not isinstance(definition["url"], str)
            or not definition["url"].startswith(("http://", "https://"))
        ):
            raise ValueError("Remote MCP servers need an HTTP(S) URL")
        for key in ("env", "environment", "headers"):
            if key in definition and (
                not isinstance(definition[key], dict)
                or any(not isinstance(v, str) for v in definition[key].values())
            ):
                raise ValueError(f"MCP {key} must contain string values")
        decoded = source.decode_server(definition)
        if decoded.get('command') or decoded.get('url'):
            definitions[source.id] = decoded
    if backend in definitions:
        return provider.encode_server(definitions[backend])
    if not definitions:
        raise ValueError('MCP server needs a command or URL')
    source = next(iter(definitions.values()))
    keys = ('url', 'headers', 'oauth') if source.get('url') else ('command', 'env')
    shared = {key: copy.deepcopy(source[key]) for key in keys if key in source}
    if not source.get('url'):
        shared.setdefault('env', {})
    return provider.encode_server(shared)


def resolve(group_id, backend):
    from src.services import groups
    get_provider(backend)
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
    for provider in providers():
        provider.clear_inherited_context(env, RUNTIME)


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
    return get_provider(backend).prepare_context(sys.modules[__name__], directory, instructions, snapshot)


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
