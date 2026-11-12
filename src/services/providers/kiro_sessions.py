import json
import os
import uuid


SESSIONS_DIR = os.path.expanduser('~/.kiro/sessions/cli')


def repair_interrupted_restart(native_id):
    path = os.path.join(SESSIONS_DIR, f'{native_id}.jsonl')
    try:
        with open(path, 'rb') as stream:
            lines = stream.readlines()
    except OSError:
        return
    completed = set()
    pending = None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        kind = obj.get('kind')
        if kind not in ('AssistantMessage', 'ToolResults'):
            continue
        expected = 'toolUse' if kind == 'AssistantMessage' else 'toolResult'
        for content in obj.get('data', {}).get('content', []):
            if not isinstance(content, dict):
                continue
            if content.get('kind') != expected:
                continue
            data = content.get('data', {})
            tool_id = data.get('toolUseId')
            if kind == 'ToolResults' and tool_id:
                completed.add(tool_id)
            if (kind == 'AssistantMessage'
                    and data.get('name') in ('mutate', 'reboot') and tool_id not in completed):
                pending = data['toolUseId']
        if pending:
            break
    if not pending:
        return
    entry = {
        'version': 'v1', 'kind': 'ToolResults', 'data': {
            'message_id': str(uuid.uuid4()), 'content': [{
                'kind': 'toolResult', 'data': {
                    'toolUseId': pending, 'content': [{'kind': 'text', 'data': json.dumps({
                        'status': 'restart_complete',
                        'message': "Fernando restarted successfully. The mutate tool call was in-flight when the old process was terminated as part of the restart sequence. This result was backfilled on session reload.",
                    })}], 'status': 'success',
                },
            }],
        },
    }
    with open(path, 'a') as stream:
        stream.write(json.dumps(entry) + '\n')
