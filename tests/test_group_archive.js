const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const {JSDOM} = require('jsdom');

test('archive groups render, search, restore without opening chats, and leave active view clean', () => {
    const dom = new JSDOM('<body><span id="switchLabelActive"></span><span id="switchLabelArchived"></span><div id="conversationSearch"><input id="archiveSearch"><button id="clearConversationSearch"></button><div id="conversationSearchStatus"></div></div><div id="sessionList"><div class="group-wrapper" id="activeGroup"><span class="group-name">Active</span><span class="group-chevron"></span><div class="group-body"></div></div></div></body>', {runScripts: 'outside-only'});
    const win = dom.window;
    const events = new Map();
    const emitted = [];
    win.socket = {on: (name, handler) => events.set(name, handler)};
    win.emitWithCsrf = (name, data) => emitted.push({name, data});
    win.eval(fs.readFileSync(path.join(__dirname, '../src/static/js/chat.js'), 'utf8'));
    win.openChatPane = () => assert.fail('Restoring a group must not open any chat');
    win.switchToArchived();
    events.get('acp_archived_list')({
        groups: [{id: 'group1', name: '<Large group>', color: '#123456', archived_members: ['chat:a', 'chat:b', 'Shell']}],
        session_groups: {'chat:a': 'group1', 'chat:b': 'group1', Shell: 'group1'},
        sessions: [{id: 'a', name: 'Parent'}, {id: 'b', name: 'Child'}, {id: 'old', name: 'Old standalone'}],
    });
    const doc = win.document;
    const group = doc.querySelector('.archived-group');
    assert.equal(group.querySelector('.group-name').textContent, '<Large group>');
    assert.equal(group.querySelectorAll('.archived-item').length, 3);
    assert.equal(doc.querySelectorAll('#sessionList > .archived-item').length, 1);
    group.querySelector('.restore-btn').click();
    assert.equal(emitted.at(-1).name, 'group_restore');
    assert.equal(emitted.at(-1).data.group_id, 'group1');
    assert.equal(emitted.filter(e => e.name === 'acp_restore' || e.name === 'acp_wake').length, 0);
    doc.getElementById('archiveSearch').value = 'Child';
    win.filterArchived();
    assert.equal(group.style.display, '');
    assert.equal(group.querySelector('[data-session="chat:b"]').style.display, '');
    assert.equal(group.querySelector('[data-session="chat:a"]').style.display, 'none');
    assert.equal(group.querySelector('.group-body').classList.contains('expanded'), true);
    doc.getElementById('archiveSearch').value = 'Large';
    win.filterArchived();
    assert.equal(group.querySelector('[data-session="chat:a"]').style.display, '');
    events.get('acp_conversation_search_results')({scope: 'archived', query: 'Child', request_id: -1, sessions: []});
    assert.equal(group.style.display, '');
    win.switchToActive();
    win.clearConversationSearch();
    assert.equal(doc.querySelectorAll('.archived-group, .archived-item').length, 0);
    assert.equal(doc.getElementById('activeGroup').style.display, '');
    dom.window.close();
});
