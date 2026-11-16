import json
import socket

from mcp.types import Tool
from _mcp_common import PROJECT_ROOT


def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STRING}
TOOLS = [
    Tool(name='job_status', description='Read the original scope, assigned requirement, ledger, feedback, and operator answers for your current job. Identity and role come from your live ACP process, not arguments.', inputSchema=schema({})),
    Tool(name='job_evidence', description='List your recorded tool event IDs, or read an original archived evidence event belonging to this job. Follow next_offset until null. Summaries and printed assertions are not proof of execution.', inputSchema=schema({'event_id': STRING, 'offset': {'type': 'integer', 'minimum': 0}})),
    Tool(name='job_plan', description='Assigned planner only: submit the complete decomposition for independent review. source is an exact quotation; dependencies are local keys. Explain nonapplicable controls/preconditions rather than omitting them.', inputSchema=schema({'requirements': {'type': 'array', 'items': schema({**{key: STRING for key in ('key', 'source', 'work', 'acceptance', 'preconditions', 'access', 'evidence', 'controls')}, 'dependencies': STRINGS}, ('key', 'source', 'work', 'acceptance', 'preconditions', 'access', 'evidence', 'controls', 'dependencies'))}}, ('requirements',))),
    Tool(name='job_submit', description='Assigned worker only: submit a candidate, never acceptance. Include exact tool inputs/outputs as evidence, relevant versions/hashes, outcome separately from execution completion, and limitations.', inputSchema=schema({'conclusion': STRING, 'limitations': STRING, 'outcome': STRING, 'versions': STRINGS, 'evidence_refs': STRINGS}, ('conclusion', 'limitations', 'outcome', 'versions', 'evidence_refs'))),
    Tool(name='job_review', description='Assigned independent reviewer only: accept or return specific unmet criteria. Inspect original evidence, controls, actual target and alternative explanations. Do not expand scope. inspected_evidence must list all candidate evidence IDs; evidence_refs lists your independent observations.', inputSchema=schema({'verdict': {'enum': ['accepted', 'needs_rework']}, 'reasons': STRING, 'inspected_evidence': STRINGS, 'evidence_refs': STRINGS}, ('verdict', 'reasons'))),
    Tool(name='job_operator', description='Request specific missing access, prerequisites, or a user decision. Blocks this requirement while the controller assigns independent work. Does not waive scope.', inputSchema=schema({'request': STRING}, ('request',))),
]


def call(name, arguments):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(20)
        client.connect(PROJECT_ROOT + '/data/jobs.sock')
        client.sendall(json.dumps({'action': name.removeprefix('job_'), 'data': arguments}).encode() + b'\n')
        with client.makefile('rb') as stream:
            response = json.loads(stream.readline(4 * 1024 * 1024))
    if 'error' in response:
        raise ValueError(response['error'])
    return response['result']
