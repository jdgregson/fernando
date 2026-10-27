const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('chat template inline scripts remain syntactically valid', () => {
    const dom = new JSDOM(fs.readFileSync(path.join(__dirname, '../src/templates/chat.html'), 'utf8'));
    for (const script of dom.window.document.querySelectorAll('script:not([src])')) {
        new (require('node:vm').Script)(script.textContent);
    }
    dom.window.close();
});

function setup(hash = '') {
    const dom = new JSDOM('<div id="messages"><div id="user" class="msg user">my-test-1</div><div id="agent">Earlier my-test-1</div><div id="match">Actual my-<b>test-1</b></div></div>', {url: 'https://fernando.test/chat/aaaaaaaa' + hash, runScripts: 'outside-only'});
    const win = dom.window;
    win.Range.prototype.getBoundingClientRect = () => ({top: 200, height: 20});
    win.CSS = {highlights: new Map()};
    win.Highlight = class { constructor(range) { this.range = range; } };
    win.eval(fs.readFileSync(path.join(__dirname, '../src/static/js/chat-search-navigation.js'), 'utf8') + '\nwindow.Navigation = ChatSearchNavigation;');
    const failures = [];
    let paused = 0;
    const navigation = new win.Navigation({messages: win.document.getElementById('messages'), pauseFollowing: () => paused++, failed: message => failures.push(message)});
    return {dom, win, navigation, failures, paused: () => paused};
}

test('opens the indexed match after history loads, not an earlier identical phrase', () => {
    const {dom, win, navigation, failures, paused} = setup('#search-event=8&search-text=my-test-1');
    navigation.register({}, win.document.getElementById('agent'), 1, 4);
    navigation.register({}, win.document.getElementById('match'), 5, 10);
    assert.equal(win.document.querySelector('.search-target'), null);
    navigation.loaded();
    assert.equal(win.document.querySelector('.search-target').id, 'match');
    assert.equal(win.CSS.highlights.get('chat-search-match').range.toString(), 'my-test-1');
    assert.equal(win.document.getElementById('messages').scrollTop, 176);
    assert.equal(paused(), 1);
    assert.deepEqual(failures, []);
    dom.window.close();
});

test('existing chat hash navigation selects the filtered actor and semantic turn', () => {
    const {dom, win, navigation} = setup();
    navigation.register({type: 'user_prompt'}, win.document.getElementById('user'), 0, 0);
    navigation.register({params: {update: {sessionUpdate: 'agent_message_chunk'}}}, win.document.getElementById('agent'), 1, 4);
    navigation.loaded();
    win.history.replaceState(null, '', '#search-turn=0&search-role=user');
    win.dispatchEvent(new win.HashChangeEvent('hashchange'));
    assert.equal(win.document.querySelector('.search-target').id, 'user');
    win.history.replaceState(null, '', '#search-turn=0&search-role=assistant');
    win.dispatchEvent(new win.HashChangeEvent('hashchange'));
    assert.equal(win.document.querySelector('.search-target').id, 'agent');
    dom.window.close();
});

test('tool result navigation opens the matching output tab and reasoning is expanded', () => {
    const {dom, win, navigation} = setup('#search-event=9&search-source=tool:rawOutput&search-text=my-test-1');
    const tool = win.document.createElement('div');
    tool.className = 'tool-call';
    tool.innerHTML = '<span class="arrow"></span><div class="tool-call-body"><div class="tc-tab active">Input</div><div class="tc-tab">Output</div><div class="tc-panel active">wrong</div><div class="tc-panel">my-test-1</div></div>';
    const panels = tool.querySelectorAll('.tc-panel');
    panels[1].style.overflowY = 'auto';
    Object.defineProperty(panels[1], 'scrollHeight', {value: 1000});
    Object.defineProperty(panels[1], 'clientHeight', {value: 300});
    tool.querySelectorAll('.tc-tab')[1].onclick = () => { panels[0].classList.remove('active'); panels[1].classList.add('active'); };
    win.document.getElementById('messages').append(tool);
    navigation.register({}, tool, 9, 9);
    navigation.loaded();
    assert.equal(tool.querySelector('.tool-call-body').classList.contains('open'), true);
    assert.equal(tool.querySelector('.tc-panel.active').textContent, 'my-test-1');
    assert.equal(panels[1].scrollTop, 188);
    const thought = win.document.getElementById('match');
    thought.style.display = 'none';
    navigation.register({}, thought, 10, 10);
    win.history.replaceState(null, '', '#search-event=10&search-source=agent_thought_chunk');
    navigation.request();
    assert.equal(thought.style.display, 'block');
    dom.window.close();
});

test('missing targets report an error rather than silently opening a different turn', () => {
    const {dom, navigation, failures} = setup('#search-event=999');
    navigation.loaded();
    assert.equal(failures.length, 1);
    dom.window.close();
});

test('previous and next visit all ten filtered occurrences without leaving the chat', () => {
    const {dom, win, navigation} = setup('#search-event=8&search-source=agent_message_chunk&search-text=needle');
    const element = win.document.getElementById('match');
    element.textContent = Array(10).fill('needle').join(' ');
    navigation.register({}, element, 8, 8);
    const requests = [];
    navigation.search = data => requests.push(data);
    const cancellations = [];
    navigation.cancel = data => cancellations.push(data);
    win.history.replaceState(null, '', '#search-event=8&search-source=agent_message_chunk&search-text=needle&search-query=%22needle%22+-user&search-scope=active');
    navigation.request();
    navigation.loaded();
    assert.equal(requests[0].query, '"needle" -user');
    const response = {...requests[0], sessions: Array.from({length: 10}, (_, index) => ({event_index: 8, source: 'agent_message_chunk', match_text: 'needle', match_occurrence: index}))};
    navigation.receive(response);
    navigation.receive({...requests[0], state: 'complete'});
    assert.equal(navigation.counter.textContent, '1 of 10');
    for (let index = 1; index < 10; index++) {
        navigation.nextButton.click();
        assert.equal(navigation.counter.textContent, `${index + 1} of 10`);
        assert.equal(win.CSS.highlights.get('chat-search-match').range.startOffset, index * 7);
    }
    assert.equal(navigation.nextButton.disabled, true);
    navigation.previousButton.click();
    assert.equal(navigation.counter.textContent, '9 of 10');
    navigation.closeButton.click();
    assert.equal(navigation.navigation.hidden, true);
    assert.equal(cancellations.length, 1);
    dom.window.close();
});
