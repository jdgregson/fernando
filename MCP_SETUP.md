# Fernando Subagent MCP Server

Add this to your Kiro CLI MCP configuration:

```json
{
  "mcpServers": {
    "fernando": {
      "command": "python3",
      "args": ["/home/coder/fernando/mcp-server.py"]
    }
  }
}
```

## Tools Available

### spawn_subagent
Spawns a new Kiro CLI instance in a tmux session with subagent instructions.

### check_subagent_status  
Checks progress of a subagent task.

### list_subagents
Lists all subagent tasks and their status.

## Subagent Workspace

Each subagent gets:
- `/home/coder/fernando/subagents/{task-id}/`
  - `task.json` - Task definition
  - `status.json` - Current progress (update this frequently)
  - `proof/screenshots/` - Screenshot proofs
  - `proof/outputs/` - JSON outputs
  - `proof/logs/` - Execution logs
  - `results/final.json` - Final deliverable
