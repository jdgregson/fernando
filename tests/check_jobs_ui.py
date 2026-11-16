import argparse
import base64
import json
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def prepare(destination):
    from flask import Flask, render_template
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / 'src/templates'))
    destination.mkdir(parents=True, exist_ok=True)
    providers = [{'id': 'kiro', 'name': 'Kiro', 'enabled': True, 'model': 'claude-sonnet-4.6'}]
    with app.app_context():
        (destination / 'index.html').write_text(render_template('jobs.html', job_id='new', api_key='fixture-key', providers=providers))
        parent = render_template('index.html', api_key='fixture-key', provider_settings={'providers': providers})
    parent = re.sub(r'<script\b[^>]*>.*?</script>', '', parent, flags=re.S)
    bootstrap = '''<script>
    const fixtureHandlers = new Map();
    window.socket = {connected:true,on(name,handler){if(!fixtureHandlers.has(name))fixtureHandlers.set(name,[]);fixtureHandlers.get(name).push(handler);}};
    window.FERNANDO_API_KEY='fixture-key';
    window._paneSession={1:null,2:null};
    window.term1=window.term2={focus(){}};
    window.doFit=window.applyProviderSettings=()=>{};
    window.showTermInPane=()=>({ready:true,firstAttach:false,wterm:{focus(){}}});
    window.fixtureRefresh=async()=>{const data=await(await fetch('/fixture/state')).json();for(const handler of fixtureHandlers.get('sessions_list')||[])handler(data);for(const handler of fixtureHandlers.get('job_changed')||[])handler({jobs:data.all_jobs});};
    window.emitWithCsrf=(name,data)=>{if(name==='get_sessions')return fixtureRefresh();if(name==='group_move_session')return fetch('/fixture/emit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,data})}).then(()=>fixtureRefresh());return Promise.resolve();};
    </script>'''
    scripts = ['modal', 'context', 'panes', 'session-adapters', 'job-session', 'sessions', 'chat', 'mobile']
    bootstrap += ''.join('<script src="/static/js/' + name + '.js"></script>' for name in scripts)
    bootstrap += '<script>paneController.installBridge();paneController.installFocusPolicy();fixtureRefresh();</script>'
    (destination / 'parent.html').write_text(parent.replace('</body>', bootstrap + '</body>'))
    assets = ['css/index.css', 'css/select.css', 'css/group-selection.css', 'css/context-menu.css', 'css/automation.css', 'js/pane-bridge.js', 'js/job-panel.js']
    assets.extend('css/' + path.name for path in (root / 'src/static/css').glob('*.css'))
    assets.extend('js/' + name + '.js' for name in scripts)
    for relative in assets:
        target = destination / 'static' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / 'src/static' / relative).read_bytes())
    (destination / 'check_jobs_ui.py').write_bytes(Path(__file__).read_bytes())


def fixture():
    spec = {'key': 'R1', 'work': 'Verify the requested operation', 'source': 'Test the target.', 'acceptance': 'Correct component and controls', 'preconditions': 'Target reachable', 'access': 'Fixture only', 'dependencies': [], 'evidence': 'Original tool events', 'controls': 'Positive and negative controls'}
    return {'id': 'a' * 32, 'name': 'Job fixture', 'state': 'running', 'paused': 0, 'max_workers': 5, 'reuse_workers': False, 'view_open': 1, 'session_revision': 1, 'draft_revision': 0, 'draft': None, 'group_id': '11111111', 'scope': 'Test the target. <img src=x onerror=window.injected=true>', 'source_sha256': 'b' * 64, 'scope_sha256': 'b' * 64, 'uploaded_source': None, 'worker': {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}, 'reviewer': {'backend': 'kiro', 'model': 'claude-sonnet-4.6'}, 'plan_accepted': 1, 'plan': None, 'updated': 1,
            'requirements': [{'id': 'r1', 'spec': spec, 'state': 'review_pending', 'waiver': None}],
            'operator_requests': [{'id': 'o1', 'requirement_id': 'r1', 'request': 'Please supply the missing target access.', 'answer': None}],
            'attempts': [{'id': 'attempt1', 'requirement_id': 'r1', 'state': 'review_pending', 'conclusion': 'Target tested', 'limitations': 'Fixture only', 'outcome': 'Defect observed', 'versions': ['sha256:' + 'd' * 64], 'evidence': ['event1']}],
            'assignments': [{'id': 'assign1', 'role': 'worker', 'state': 'finished', 'requirement_id': 'r1', 'session_id': '1234abcd'}],
            'reviews': [{'id': 'review1', 'revision': 1, 'verdict': 'accepted', 'reasons': 'Original scope fully represented', 'attempt_id': None}],
            'job_events': [{'created': time.time(), 'kind': 'created', 'detail': 'Fixture started'}]}


def run(directory, integration=False, agents=False):
    import websocket
    job = fixture()
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, payload, content_type='application/json', status=200):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type + ('; charset=utf-8' if content_type.startswith('text/') else ''))
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.split('?')[0] == '/workspace':
                self.send((directory / 'parent.html').read_bytes(), 'text/html')
            elif self.path == '/fixture/state':
                self.send({'sessions': [], 'chat_sessions': [], 'job_sessions': [job] if job['view_open'] else [], 'all_jobs': [job], 'groups': [{'id': '11111111', 'name': 'Fixture group', 'color': '#b8860b'}, {'id': '22222222', 'name': 'Second group', 'color': '#336699'}], 'session_groups': {'job:' + job['id']: job['group_id']} if job['group_id'] else {}})
            elif self.path == '/api/jobs':
                self.send({'jobs': [job]})
            elif self.path.startswith('/api/providers/'):
                self.send({'models': [{'id': 'claude-sonnet-4.6', 'name': 'Sonnet 4.6'}]})
            elif '/evidence/' in self.path:
                self.send({'sha256': 'c' * 64, 'event': {'tool': 'fixture', 'result': 'Observed result <script>unsafe</script>'}})
            elif self.path.startswith('/api/jobs/'):
                self.send(job)
            elif self.path.startswith('/static/'):
                path = directory / self.path.lstrip('/')
                if not path.is_file():
                    self.send(b'', 'text/plain')
                    return
                self.send(path.read_bytes(), 'text/css' if path.suffix == '.css' else 'text/javascript')
            elif self.path.startswith('/jobs/'):
                identity = self.path.split('?')[0].split('/')[-1]
                config = {'id': identity, 'apiKey': 'fixture-key', 'providers': [{'id': 'kiro', 'name': 'Kiro', 'enabled': True, 'model': 'claude-sonnet-4.6'}], 'job': None if identity == 'new' else job}
                encoded = json.dumps(config).replace('<', '\\u003c')
                html = re.sub(r'window.jobConfig = .*?;</script>', lambda match: 'window.jobConfig = ' + encoded + ';</script>', (directory / 'index.html').read_text(), flags=re.S)
                self.send(html.encode(), 'text/html')
            else:
                self.send(b'', 'text/plain')

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/fixture/emit':
                assert data['name'] == 'group_move_session' and data['data']['session_key'] == 'job:' + job['id']
                job['group_id'] = data['data']['group_id']
            elif self.path == '/api/jobs/drafts':
                job['state'] = 'draft'
                job['paused'] = 1
                job['view_open'] = 1
                job['name'] = 'New Job'
                job['group_id'] = data.get('group_id')
                job['draft_revision'] = 0
                job['draft'] = {'name': '', 'scope': '', 'source_id': None, 'source_name': '', 'worker': job['worker'], 'reviewer': job['reviewer'], 'max_workers': 5, 'reuse_workers': False}
            elif self.path.endswith('/draft'):
                if data['revision'] != job['draft_revision']:
                    self.send({'error': 'Draft changed in another view'}, status=400)
                    return
                job['draft'] = data['draft']
                job['max_workers'] = data['draft']['max_workers']
                job['reuse_workers'] = data['draft']['reuse_workers']
                job['draft_revision'] += 1
                job['name'] = data['draft']['name'] or 'New Job'
                if data.get('submit'):
                    job['scope'] = data['draft']['scope']
                    job['state'] = 'running'
                    job['paused'] = 0
            elif self.path == '/api/jobs':
                job['name'] = data['name']
                job['scope'] = data['scope']
            elif self.path.endswith('/operator'):
                if data['action'] == 'pause':
                    job['paused'] = 1
                elif data['action'] == 'resume':
                    job['paused'] = 0
                elif data['action'] == 'answer':
                    job['operator_requests'][0]['answer'] = data['answer']
                elif data['action'] == 'waive':
                    job['requirements'][0]['state'] = 'waived'
                    job['requirements'][0]['waiver'] = data['reason']
                elif data['action'] in ('open', 'close'):
                    job['view_open'] = int(data['action'] == 'open')
                elif data['action'] == 'rename':
                    job['name'] = data['name']
                    if job['state'] == 'draft':
                        job['draft']['name'] = data['name']
                        job['draft_revision'] += 1
            job['updated'] += 1
            job['session_revision'] += 1
            self.send(job)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:' + str(server.server_port) + ('/workspace' if integration or agents else '/jobs/new')
    target = json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:9222/json/new?' + url, method='PUT')))
    ws = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=15, suppress_origin=True)
    sequence = 0

    def command(method, params=None):
        nonlocal sequence
        sequence += 1
        ws.send(json.dumps({'id': sequence, 'method': method, 'params': params or {}}))
        while True:
            result = json.loads(ws.recv())
            if result.get('method') == 'Runtime.exceptionThrown':
                errors.append(result)
            if result.get('id') == sequence:
                if 'error' in result:
                    raise RuntimeError(result['error'])
                return result.get('result', {})

    def js(expression):
        result = command('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        if 'exceptionDetails' in result:
            raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')

    def wait(expression):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if js('Boolean(' + expression + ')'):
                return
            time.sleep(0.1)
        image = command('Page.captureScreenshot', {'format': 'png'})
        (directory / 'job-ui-failure.png').write_bytes(base64.b64decode(image['data']))
        raise RuntimeError('Timed out: ' + expression + '\nBrowser exceptions: ' + json.dumps(errors))

    checks = []
    touch_observations = []
    try:
        command('Runtime.enable')
        command('Page.bringToFront')
        if agents:
            wait("document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"]')")
            command('Emulation.setDeviceMetricsOverride', {'width': 1100, 'height': 850, 'deviceScaleFactor': 1, 'mobile': False})
            js("""(()=>{
                localStorage.removeItem('fernando_collapsed_parents');
                const id='a'.repeat(32);
                const agents=Array.from({length:100},(_,i)=>({id:(i+1000).toString(16).padStart(8,'0'),name:'Worker '+i+' '+('long task description '.repeat(20)),job_id:id,job_role:'worker',is_subagent:true,loaded:false,status:'idle'}));
                const ordinary={id:'ffffffff',name:'Standalone conversation',is_subagent:false,loaded:false};
                const fork={id:'fffffffe',name:'Independent fork',is_subagent:false,session_kind:'fork',loaded:false};
                const helper={id:'fffffffd',name:'General helper',is_subagent:true,parent_id:'ffffffff',session_kind:'subagent',loaded:false};
                window.fixtureAgentRows=agents;
                window.fixtureArchiveRows=[ordinary,fork,helper,...agents];
                updateSessionList([], [ordinary,...agents], {job_sessions:[{id,name:'Job with 100 agents',state:'running',view_open:1,paused:0,session_revision:100}],groups:[{id:'11111111',name:'Fixture group',color:'#b8860b'}],session_groups:{['job:'+id]:'11111111'}});
                const prior=window.emitWithCsrf;
                window.emitWithCsrf=(name,data)=>{
                    if(name==='acp_list_archived'){for(const h of fixtureHandlers.get('acp_archived_list'))h({sessions:window.fixtureArchiveRows,groups:[],session_groups:{}});return;}
                    if(name==='acp_search_conversations'){for(const h of fixtureHandlers.get('acp_conversation_search_results'))h({...data,state:'complete',total:103,completed:103,sessions:[{id:agents[0].id,snippet:'hidden evidence',match:'Tool content'}]});return;}
                    return prior(name,data);
                };
            })()""")
            assert js("document.querySelectorAll('.job-agent-row').length===100")
            metrics = js("[...document.querySelectorAll('.job-agent-row')].map(row=>{const s=getComputedStyle(row),label=row.querySelector('.session-name > span');return {padding:[s.paddingTop,s.paddingRight,s.paddingBottom,s.paddingLeft],height:row.getBoundingClientRect().height,nowrap:getComputedStyle(label).whiteSpace,ellipsis:getComputedStyle(label).textOverflow,truncated:label.scrollWidth>label.clientWidth};})")
            assert all(row['padding'] == ['0px'] * 4 and row['height'] < 25 and row['nowrap'] == 'nowrap' and row['ellipsis'] == 'ellipsis' and row['truncated'] for row in metrics), metrics[:2]
            checks.append('100 owned agents have zero row padding and single-line ellipsis')
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / 'job-agents-expanded.png').write_bytes(base64.b64decode(image['data']))
            js("document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"] .parent-chevron').click()")
            assert js("document.querySelectorAll('.job-agent-row').length===0 && document.querySelector('[data-session=\"chat:ffffffff\"]')!==null")
            checks.append('Job collapse hides its agents without hiding unrelated chats')
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / 'job-agents-collapsed.png').write_bytes(base64.b64decode(image['data']))
            js("document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"] .parent-chevron').click()")
            assert js("document.querySelectorAll('.job-agent-row').length===100")
            js("document.getElementById('switchLabelArchived').click()")
            visible = "[...document.querySelectorAll('.archived-item')].filter(row=>row.style.display!=='none').length"
            assert js(visible) == 2
            checks.append('archived Job agents and ordinary subagents are hidden by default; forks remain visible')
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / 'archive-subagents-hidden.png').write_bytes(base64.b64decode(image['data']))
            js("document.getElementById('includeArchivedSubagents').click()")
            assert js(visible) == 103
            js("document.getElementById('includeArchivedSubagents').click()")
            assert js(visible) == 2
            checks.append('Include subagents checkbox toggles all archived subagents')
            js("const input=document.getElementById('archiveSearch');input.value='hidden evidence';input.dispatchEvent(new Event('input',{bubbles:true}))")
            wait(visible + '===1')
            assert js("document.querySelector('[data-session=\"chat:000003e8\"].archived-item').style.display!== 'none' && !document.getElementById('includeArchivedSubagents').checked")
            assert js("document.querySelector('[data-session=\"chat:000003e8\"].archived-item').getBoundingClientRect().height<25")
            checks.append('matching content search reveals a hidden subagent without changing the checkbox')
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / 'archive-subagent-search.png').write_bytes(base64.b64decode(image['data']))
            js("document.getElementById('clearConversationSearch').click()")
            assert js(visible) == 2
            checks.append('clearing search restores the default hidden-subagent view')
            assert not errors, errors
            (directory / 'agent-results.json').write_text(json.dumps({'checks': checks, 'row_metrics': metrics[:2], 'exceptions': errors}, indent=2))
            print(json.dumps({'passed': len(checks), 'checks': checks}))
            return
        if integration:
            wait("document.querySelector('[data-session=\"job:' + 'a'.repeat(32) + '\"]')")
            js("openNewSessionModal('11111111')")
            js("document.querySelector('[onclick=\"createJobSession()\"]').click()")
            wait("document.getElementById('jobPickerModal').classList.contains('open') && document.getElementById('jobPickerSelect').options.length===1")
            js("document.querySelector('[onclick=\"createNewJob()\"]').click()")
            wait("document.querySelector('#browser1 iframe')?.contentDocument.querySelector('#workerModel')?.options.length===1")
            wait("document.querySelector('#browser1 iframe').contentDocument.querySelector('#reviewerModel').options.length===1")
            assert job['group_id'] == '11111111'
            checks.append('picker creation preserves selected group')
            js("toggleSplit(); paneController.open('job:'+'a'.repeat(32))")
            wait("document.querySelector('#browser2 iframe')?.contentDocument.querySelector('#workerModel')?.options.length===1")

            def click_in(pane, selector, touch=False):
                coordinates = js("(()=>{const f=document.querySelector('#browser" + str(pane) + " iframe');const e=f.contentDocument.querySelector(" + json.dumps(selector) + ");e.scrollIntoView({block:'center'});const r=f.getBoundingClientRect();const b=e.getBoundingClientRect();return {x:r.x+b.x+b.width/2,y:r.y+b.y+b.height/2};})()")
                if touch:
                    js("(()=>{const w=document.querySelector('#browser1 iframe').contentWindow;w.fixtureTouches=[];for(const type of ['touchstart','touchend','pointerdown','pointerup','pointercancel','click'])w.document.addEventListener(type,e=>w.fixtureTouches.push({type,target:e.target.id,x:e.clientX,y:e.clientY,prevented:e.defaultPrevented}),true);})()")
                    command('Input.synthesizeTapGesture', {**coordinates, 'duration': 100, 'gestureSourceType': 'touch'})
                    touch_observations.append({'coordinates': coordinates, 'observed': js("({events:document.querySelector('#browser1 iframe').contentWindow.fixtureTouches,width:innerWidth,height:innerHeight,scale:visualViewport.scale,focus:document.hasFocus(),visibility:document.visibilityState,active:activeTerminal})")})
                else:
                    command('Input.dispatchMouseEvent', {'type': 'mousePressed', 'button': 'left', 'clickCount': 1, **coordinates})
                    command('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'button': 'left', 'clickCount': 1, **coordinates})

            command('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
            for pane, selector in [(1, '#jobName'), (2, '#jobScope'), (1, 'header')]:
                js('setActiveTerminal(' + str(3 - pane) + ',false)')
                click_in(pane, selector)
                wait('activeTerminal===' + str(pane))
                image = command('Page.captureScreenshot', {'format': 'png'})
                (directory / ('job-focus-' + str(pane) + '-' + selector.replace('#', '') + '.png')).write_bytes(base64.b64decode(image['data']))
            checks.append('real mouse clicks activate the owning pane in both directions')
            assert js("document.querySelector('#browser1 iframe').contentDocument.documentElement.style.getPropertyValue('--group-selection-background')==='#b8860b'")
            checks.append('actual shared bridge receives group styling')
            click_in(1, '#jobName')
            command('Input.insertText', {'text': 'Native saved draft'})
            click_in(1, '#jobScope')
            command('Input.insertText', {'text': 'Keep this unsent scope across reload.'})
            wait("document.querySelector('#browser1 iframe').contentDocument.getElementById('jobError').textContent==='' && document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"] .session-name').textContent==='Native saved draft'")
            wait("document.querySelector('#browser2 iframe').contentDocument.getElementById('jobScope').value==='Keep this unsent scope across reload.'")
            checks.append('draft autosave updates the other live view')
            js("(()=>{const item=document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"]');const transfer=new DataTransfer();item.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));if(transfer.getData('application/x-session-key')!=='job:'+'a'.repeat(32))throw Error('Missing shared drag binding');document.querySelector('[data-group-id=\"22222222\"] .group-header').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer}));item.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:transfer}));})()")
            wait("document.querySelector('#browser1 iframe').contentDocument.documentElement.style.getPropertyValue('--group-selection-background')==='#336699'")
            wait("document.querySelector('#browser2 iframe').contentDocument.documentElement.style.getPropertyValue('--group-selection-background')==='#336699'")
            assert job['group_id'] == '22222222'
            checks.append('shared drag/drop changes group and both embedded selection colors')
            js("document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"]').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:40,clientY:100}))")
            wait("document.querySelector('.group-context-menu')")
            labels = js("[...document.querySelectorAll('.group-context-menu .context-menu-item')].map(e=>e.textContent)")
            assert 'Rename' in labels and 'Close' in labels and 'Set page title' in labels
            assert not any(label in labels for label in ('Archive', 'Fork', 'Sleep', 'Wake', 'Download'))
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / 'job-session-menu.png').write_bytes(base64.b64decode(image['data']))
            js("[...document.querySelectorAll('.group-context-menu .context-menu-item')].find(e=>e.textContent==='Rename').click()")
            wait("document.querySelector('.session-rename-input')")
            js("(()=>{const input=document.querySelector('.session-rename-input');input.value='Renamed Job';input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()")
            wait("document.querySelector('#browser1 iframe').contentDocument.getElementById('jobName').value==='Renamed Job'")
            wait("document.querySelector('#browser2 iframe').contentDocument.getElementById('jobName').value==='Renamed Job'")
            checks.append('normal session rename updates both views without ACP menu actions')
            js("document.querySelector('[data-session=\"job:'+'a'.repeat(32)+'\"]').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:40,clientY:100}));[...document.querySelectorAll('.group-context-menu .context-menu-item')].find(e=>e.textContent==='Set page title').click()")
            assert js("document.title==='Renamed Job'")
            checks.append('shared custom page title works for Job')
            js("window.fixtureFrame=document.querySelector('#browser1 iframe');setActiveTerminal(1,false);paneController.open('job:'+'a'.repeat(32))")
            assert js("window.fixtureFrame===document.querySelector('#browser1 iframe')")
            checks.append('reselection retains the same embedded document')
            command('Page.reload')
            wait("document.querySelector('#browser1 iframe')?.contentDocument.getElementById('jobScope')?.value==='Keep this unsent scope across reload.'")
            wait("document.querySelector('#browser2 iframe')?.contentDocument.getElementById('jobScope')?.value==='Keep this unsent scope across reload.'")
            assert js("document.title==='Renamed Job'")
            assert js('activeTerminal===1')
            checks.append('URL restoration reloads both draft views and their saved contents')
            js("paneController.sessionTypes.get('job').close('job:'+'a'.repeat(32))")
            wait("paneController.get(1).surface==='terminal' && paneController.get(2).surface==='terminal'")
            assert job['state'] == 'draft' and job['draft']['scope'] == 'Keep this unsent scope across reload.'
            js("openNewSessionModal('11111111');createJobSession()")
            wait("document.getElementById('jobPickerOpen').disabled===false")
            js("openSelectedJob()")
            wait("document.querySelector('#browser1 iframe')?.contentDocument.getElementById('jobScope')?.value==='Keep this unsent scope across reload.'")
            checks.append('close and reopen retain data without chat archival')
            command('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
            command('Emulation.setTouchEmulationEnabled', {'enabled': True})
            js('setActiveTerminal(2,true)')
            assert js('activeTerminal===2')
            js('new Promise(resolve=>setTimeout(resolve,400))')
            click_in(1, 'header', touch=True)
            touch_result = {'status': 'passed'}
            if not js('activeTerminal===1'):
                control = js("(()=>{const b=document.querySelector('[onclick=\"toggleSplit()\"]');window.fixtureControlTap=false;b.onclick=()=>{window.fixtureControlTap=true};const r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
                command('Input.synthesizeTapGesture', {**control, 'duration': 100, 'gestureSourceType': 'touch'})
                js('new Promise(resolve=>setTimeout(resolve,500))')
                control_passed = js('window.fixtureControlTap')
                assert not control_passed, 'Host tap control succeeded but Job activation failed'
                touch_result = {'status': 'unavailable', 'reason': 'Native touch synthesis produced no click on either the Job document or the ordinary host-button positive control', 'control_clicked': control_passed, 'observations': touch_observations}
                command('Emulation.setTouchEmulationEnabled', {'enabled': False})
                click_in(1, '#jobName')
            wait('activeTerminal===1')
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / 'job-mobile-layout.png').write_bytes(base64.b64decode(image['data']))
            checks.append('mobile layout and document activation')
            assert not errors, errors
            (directory / 'integration-results.json').write_text(json.dumps({'checks': checks, 'exceptions': errors, 'native_touch': touch_result}, indent=2))
            print(json.dumps({'passed': len(checks), 'checks': checks, 'native_touch': touch_result['status']}))
            return
        wait("document.querySelector('#workerModel')?.options.length === 1 && document.querySelector('#reviewerModel')?.options.length === 1")
        assert js("document.getElementById('jobMaxWorkers').value==='5' && !document.getElementById('jobReuseWorkers').checked")
        checks.append('execution defaults are five workers and fresh worker attempts')
        for width in (1440, 390, 320):
            command('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 850, 'deviceScaleFactor': 1, 'mobile': width < 500})
            wait('document.documentElement.clientWidth === ' + str(width))
            assert js('document.documentElement.scrollWidth <= innerWidth')
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / f'job-create-{width}.png').write_bytes(base64.b64decode(image['data']))
            checks.append(f'create layout {width}')
        js("document.getElementById('jobName').value='Evidence review'; document.getElementById('jobScope').value='Test the target. <img src=x onerror=window.injected=true>'; document.getElementById('jobMaxWorkers').value='2'; document.getElementById('jobReuseWorkers').click(); document.getElementById('jobCreateButton').click()")
        wait("document.querySelector('#jobCreate').hidden && document.querySelector('#jobView').textContent.includes('0 / 1')")
        assert not js('Boolean(window.injected)')
        assert job['max_workers'] == 2 and job['reuse_workers'] is True
        assert js("document.getElementById('jobView').textContent.includes('Max workers: 2')")
        checks.append('selected concurrency and reuse settings persist into the job')
        checks.append('create job and safe scope rendering')
        js("document.querySelector('details[data-key=scope]').open=true; document.querySelector('details[data-key=r1]').open=true; document.querySelector('details[data-key=attempt1]').open=true")
        js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Evidence event1').click()")
        wait("document.querySelector('#jobView').textContent.includes('Observed result <script>unsafe</script>')")
        checks.append('original evidence retrieval and safe rendering')
        for width in (1440, 390, 320):
            command('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 850, 'deviceScaleFactor': 1, 'mobile': width < 500})
            wait('document.documentElement.clientWidth === ' + str(width))
            assert js("document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth")
            image = command('Page.captureScreenshot', {'format': 'png'})
            (directory / f'job-panel-{width}.png').write_bytes(base64.b64decode(image['data']))
            checks.append(f'ledger layout {width}')
        js("document.querySelector('textarea[data-draft]').value='Unsaved operator answer'; [...document.querySelectorAll('button')].find(b=>b.textContent==='Pause dispatch').click()")
        wait("document.getElementById('jobState').textContent==='paused'")
        assert js("document.querySelector('textarea[data-draft]').value==='Unsaved operator answer'")
        checks.append('pause and preserve operator draft')
        js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Resume').click()")
        wait("document.getElementById('jobState').textContent==='running'")
        js("[...document.querySelectorAll('button')].find(b=>b.textContent==='Answer and retry').click()")
        wait("document.querySelector('#jobView').textContent.includes('Resolved request')")
        checks.append('resume and answer operator request')
        js("document.querySelector('details[data-key=waive-r1]').open=true; document.querySelector('details[data-key=waive-r1] textarea').value='User deliberately drops fixture obligation'; [...document.querySelectorAll('button')].find(b=>b.textContent==='Confirm waiver').click()")
        wait("document.querySelector('#jobView').textContent.includes('1 / 1')")
        checks.append('explicit user waiver')
        assert not errors, errors
        (directory / 'results.json').write_text(json.dumps({'checks': checks, 'exceptions': errors}, indent=2))
        print(json.dumps({'passed': len(checks), 'checks': checks}))
    finally:
        ws.close()
        urllib.request.urlopen('http://127.0.0.1:9222/json/close/' + target['id']).close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--integration', action='store_true')
    parser.add_argument('--agents', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare)
    else:
        run(args.run, args.integration, args.agents)
