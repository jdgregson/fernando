# Agent providers

Fernando's chat providers are registered in `src/services/providers/__init__.py`.
Kiro, OpenCode, and Claude Code use the same ACP transport and session manager.

## Shared responsibilities

`src/services/acp.py` owns JSON-RPC requests, streaming, cancellation, process
supervision, rich history, sleeping and waking, restart recovery, group membership,
and registering forked conversations. Standard `initialize`, `session/new`,
`session/load`, `session/prompt`, and `session/cancel` operations stay here.

`context_templates.py` resolves group templates and selected steering documents,
writes private per-chat context, and converts MCP definitions through provider
adapters. The shared MCP representation uses an argument-array `command`, `env`,
or a remote `url` with `headers` and `oauth`. Existing provider-specific saved
definitions remain supported and take precedence for their own provider.

## Adapter contract

Implement `Provider` in a new module and register its instance:

| Member | Responsibility |
| --- | --- |
| `id`, `name`, settings keys, default model | Stable identity and configuration |
| `capabilities` | Fork, selected-turn fork, model switching, effort, cost, quota, native slash commands |
| `launch` | Return command and environment; the common manager spawns the process |
| `prepare_context` | Write provider-specific configuration for the selected context |
| `decode_server`, `encode_server` | Translate MCP configuration to and from the shared representation |
| `global_mcp`, `import_servers` | Locate persistent MCP settings and import existing definitions |
| `list_models`, `normalize_models` | Native model discovery and normalized UI choices |
| `prepare_resume`, `can_resume` | Native resume preparation and availability checks |
| `session_parameters` | Standard new/load parameters, with optional provider overrides |
| `check_mcp_startup`, `handle_notification` | Provider-specific health confirmation and extension notifications |
| `fork_session` | Create a native fork; supplied history/boundary identifies a selected user turn |
| `execute_command` | Optional native slash-command extension |
| `context_paths`, `context_flags` | Remove Fernando-owned inherited context overrides from shell launches |

Adapters are shared, stateless instances. Per-process credentials belong to the
session, never the adapter. Preserve loopback authentication and scoped context
when adding a backend. Unsupported operations must fail explicitly.

Settings cards, new-chat cards, automation provider choices, and session metadata
consume registry metadata. The backend chooses the configured default model when
creating a session. Existing persisted provider IDs and setting keys are retained.

## HTTP API

All endpoints require Fernando's API key.

- `GET /api/providers`: metadata, capabilities, enabled state, and configured models.
- `GET /api/providers/<id>/models`: `models` array with `id` and `name` fields.
- `/api/models` and `/api/opencode_models`: existing response formats retained for callers.

Model discovery is cached independently per provider for five minutes. Discovery
failures return an error rather than inventing model choices.

## Verification

`tests/test_providers.py` registers a third fixture provider and exercises shared
launch/resume, context, model APIs, authentication, unsupported operations, and
provider-driven templates. Set `NODE` to the Node executable for inline-script
syntax checks.

Run context, fork, MCP, archive, and restart regressions alongside it. Native
context/resume checks use `RUN_CONTEXT_INTEGRATION=1`; native OpenCode fork checks
use `RUN_OPENCODE_INTEGRATION=1`. OpenCode model responses in these checks come
from a local fixture server.

Adapter or shared ACP changes require a full Fernando runtime restart. A web-only
restart does not reload existing agent processes.

## Claude Code

Enable **Claude Code** under Settings > Providers. `scripts/update-backends.sh`
runs `scripts/update-claude-code.py` when `providers_claude_code` is true. The
updater downloads the latest stable `agentclientprotocol/claude-agent-acp` Linux
release, verifies its GitHub SHA-256 digest and size, verifies both executable
versions, and atomically activates the adapter and bundled Claude CLI together.
It retains three release directories under
`~/.local/share/fernando/claude-code/versions`.

Sign in from a Fernando terminal:

```sh
~/.local/share/fernando/claude-code/current/claude auth login
```

The provider is disabled by default. Model choices come from the installed
adapter's ACP `session/options` response, including while signed out. Creating
a signed-out chat reports the login command before allocating a conversation.
Claude's normal credential storage is used; Fernando does not copy credentials
into chat context files.

Selected tools are supplied explicitly through the SDK with strict MCP
configuration and no user/project/local settings sources. Selected steering is
loaded from the private per-chat steering file, avoiding command-line argument
size limits. Prompt snapshots are disabled so applying new context takes effect
on resume. Native conversation storage remains in Claude's configuration
directory. Full-conversation forks are supported; selected-turn forks are not
exposed for this provider.

`RUN_CLAUDE_INTEGRATION=1` enables the native integration test in
`tests/test_claude_code.py`. It uses the installed adapter and CLI with a local
Anthropic-compatible fixture, isolated credentials and native history, and a
real fixture MCP server. It exercises streaming, deferred tool discovery, tool
execution, populated resume, steering refresh, and native forking without a
Claude subscription or a request to Anthropic.
