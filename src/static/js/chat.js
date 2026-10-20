// --- ACP Chat Sessions ---

function createChatSession() {
    const groupId = typeof getNewSessionGroupId === 'function' ? getNewSessionGroupId() : getActivePaneGroupId();
    closeNewSessionModal();
    emitWithCsrf('acp_create', groupId ? { group_id: groupId } : {});
}

function createOpenCodeChatSession() {
    const groupId = typeof getNewSessionGroupId === 'function' ? getNewSessionGroupId() : getActivePaneGroupId();
    closeNewSessionModal();
    fetch('/api/settings?api_key=' + window.FERNANDO_API_KEY)
        .then(r => r.json())
        .then(data => {
            const model = data.opencode_model || 'amazon-bedrock/us.anthropic.claude-opus-4-6-v1';
            emitWithCsrf('acp_create', {
                backend: 'opencode',
                model: model,
                group_id: groupId
            });
        })
        .catch(() => {
            emitWithCsrf('acp_create', {
                backend: 'opencode',
                model: 'amazon-bedrock/us.anthropic.claude-opus-4-6-v1',
                group_id: groupId
            });
        });
}

socket.on('acp_created', (data) => { openChatPane(data.session_id); });

function openChatPane(chatId) {
    return paneController.open('chat:' + chatId);
}

function mountChat({ key, browser }) {
    const chatId = key.slice(5);
    const existing = browser.querySelector('iframe');
    if (!existing || !existing.src.includes('/chat/' + chatId)) {
        browser.innerHTML = '';
        const iframe = document.createElement('iframe');
        iframe.src = '/chat/' + chatId + '?api_key=' + encodeURIComponent(window.FERNANDO_API_KEY);
        iframe.style.cssText = 'width:100%;height:100%;border:none';
        browser.appendChild(iframe);
    }
}

function closeChatSession(chatId) {
    return paneController.close('chat:' + chatId);
}

// --- Archived ---
let showArchived = false;
const expandedArchivedGroups = new Set();
let conversationSearchRequest = 0;
let conversationSearchMatches = new Map();
function clearConversationSearch() {
    document.getElementById('archiveSearch').value = '';
    filterArchived();
    document.getElementById('archiveSearch').focus();
}
function highlightSearchText(element, text, query) {
    element.replaceChildren();
    const tokens = query.toLocaleLowerCase().match(/[\p{L}\p{N}_]+/gu) || [];
    const lower = text.toLocaleLowerCase();
    let start = 0;
    while (start < text.length) {
        const matches = tokens.map(token => ({index: lower.indexOf(token, start), length: token.length})).filter(match => match.index >= 0).sort((a, b) => a.index - b.index || b.length - a.length);
        if (!matches.length) {
            element.append(document.createTextNode(text.slice(start)));
            break;
        }
        const match = matches[0];
        element.append(document.createTextNode(text.slice(start, match.index)));
        const mark = document.createElement('mark');
        mark.textContent = text.slice(match.index, match.index + match.length);
        element.append(mark);
        start = match.index + match.length;
    }
}
function switchToActive() {
    if (!showArchived) return;
    showArchived = false;
    document.getElementById('switchLabelActive').classList.add('active');
    document.getElementById('switchLabelArchived').classList.remove('active');
    const search = document.getElementById('archiveSearch');
    search.placeholder = 'Search active conversations…';
    document.querySelectorAll('#sessionList > .session-item:not(.archived-item)').forEach(el => el.style.display = '');
    document.querySelectorAll('#sessionList > .group-wrapper:not(.archived-group)').forEach(el => el.style.display = '');
    document.querySelectorAll('#sessionList > .new-group-btn').forEach(el => el.style.display = '');
    document.querySelectorAll('.archived-item').forEach(el => el.remove());
    document.querySelectorAll('.archived-group').forEach(el => el.remove());
    filterArchived();
}
function switchToArchived() {
    if (showArchived) return;
    showArchived = true;
    document.getElementById('switchLabelActive').classList.remove('active');
    document.getElementById('switchLabelArchived').classList.add('active');
    const search = document.getElementById('archiveSearch');
    search.placeholder = 'Search archived conversations…';
    filterArchived();
    document.querySelectorAll('#sessionList > .session-item:not(.archived-item)').forEach(el => el.style.display = 'none');
    document.querySelectorAll('#sessionList > .group-wrapper').forEach(el => el.style.display = 'none');
    document.querySelectorAll('#sessionList > .new-group-btn').forEach(el => el.style.display = 'none');
    emitWithCsrf('acp_list_archived');
}
function filterArchived() {
    const q = document.getElementById('archiveSearch').value.trim();
    clearTimeout(filterArchived._timer);
    const requestId = ++conversationSearchRequest;
    conversationSearchMatches = new Map();
    document.getElementById('conversationSearch').classList.toggle('searching', !!q);
    document.getElementById('clearConversationSearch').hidden = !q;
    document.getElementById('conversationSearchStatus').hidden = !q;
    filterArchivedEntries(q);
    if (!q) {
        return;
    }
    filterArchived._timer = setTimeout(() => {
        document.getElementById('conversationSearchStatus').textContent = 'Searching ' + (showArchived ? 'archived' : 'active') + ' conversations…';
        emitWithCsrf('acp_search_conversations', { query: q, scope: showArchived ? 'archived' : 'active', request_id: requestId });
    }, 300);
}

function filterArchivedEntries(query) {
    const quoted = query.match(/^"([^"]+)"$|^“([^”]+)”$/u);
    const q = (quoted ? quoted[1] || quoted[2] : query).toLowerCase();
    let count = 0;
    const ranges = [];
    document.querySelectorAll(showArchived ? '.archived-item' : '#sessionList .session-item:not(.archived-item)').forEach(item => {
        const group = item.closest('.group-wrapper');
        const groupMatch = !quoted && group && group.querySelector('.group-name').textContent.toLowerCase().includes(q);
        const name = item.querySelector('.session-name');
        if (!name) return;
        const hit = conversationSearchMatches.get(item.dataset.session);
        const matched = !q || groupMatch || name.textContent.toLowerCase().includes(q) || hit;
        item.style.display = matched ? '' : 'none';
        if (matched) count++;
        if (q) {
            const walker = document.createTreeWalker(name, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const tokens = q.match(/[\p{L}\p{N}_]+/gu) || [];
                for (const token of tokens) {
                    let offset = node.textContent.toLowerCase().indexOf(token);
                    while (offset >= 0) {
                        const range = document.createRange();
                        range.setStart(node, offset);
                        range.setEnd(node, offset + token.length);
                        ranges.push(range);
                        offset = node.textContent.toLowerCase().indexOf(token, offset + token.length);
                    }
                }
            }
        }
        item.querySelector('.conversation-search-preview')?.remove();
        item.classList.toggle('search-result', !!q && !!matched);
        if (q && matched && hit?.snippet) {
            const preview = document.createElement('div');
            preview.className = 'conversation-search-preview';
            preview.title = hit.match;
            highlightSearchText(preview, hit.snippet, query);
            item.append(preview);
        }
    });
    document.querySelectorAll(showArchived ? '.archived-group' : '#sessionList .group-wrapper:not(.archived-group)').forEach(group => {
        const nameMatch = !quoted && group.querySelector('.group-name').textContent.toLowerCase().includes(q);
        const childMatch = Array.from(group.querySelectorAll('.session-item')).some(item => item.style.display !== 'none');
        group.style.display = !q || nameMatch || childMatch ? '' : 'none';
        if (q && !group.hasAttribute('data-search-expanded')) group.dataset.searchExpanded = String(group.querySelector('.group-body').classList.contains('expanded'));
        if (q && childMatch) {
            group.querySelector('.group-body').classList.add('expanded');
            group.querySelector('.group-chevron').classList.add('expanded');
        }
        if (!q && group.hasAttribute('data-search-expanded')) {
            const expanded = group.dataset.searchExpanded === 'true';
            group.querySelector('.group-body').classList.toggle('expanded', expanded);
            group.querySelector('.group-chevron').classList.toggle('expanded', expanded);
            delete group.dataset.searchExpanded;
        }
    });
    document.getElementById('conversationSearchStatus').textContent = count + (quoted ? ' exact matches in ' : ' matches in ') + (showArchived ? 'archived' : 'active') + ' · Esc to clear';
    if (window.CSS?.highlights) CSS.highlights.set('conversation-search', new Highlight(...ranges));
}

socket.on('acp_conversation_search_results', (data) => {
    if (data.scope !== (showArchived ? 'archived' : 'active') || data.request_id !== conversationSearchRequest) return;
    const q = document.getElementById('archiveSearch').value.trim();
    if (!q || data.query !== q) return;
    const sessions = data.sessions || [];
    conversationSearchMatches = new Map(sessions.map(s => ['chat:' + s.id, s]));
    filterArchivedEntries(q);
});

socket.on('acp_archived_list', (data) => {
    document.querySelectorAll('.archived-item').forEach(el => el.remove());
    document.querySelectorAll('.archived-group').forEach(el => el.remove());
    if (!showArchived) return;
    const list = document.getElementById('sessionList');
    const groupBodies = new Map();
    const groupedMembers = new Set();
    (data.groups || []).forEach(group => {
        const wrapper = document.createElement('div');
        wrapper.className = 'group-wrapper archived-group';
        wrapper.dataset.groupId = group.id;
        wrapper.style.setProperty('--group-color', group.color);
        wrapper.style.background = group.color + '33';
        const header = document.createElement('div');
        header.className = 'group-header';
        const chevron = document.createElement('span');
        chevron.className = 'group-chevron' + (expandedArchivedGroups.has(group.id) ? ' expanded' : '');
        chevron.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="3 2 7 5 3 8"/></svg>';
        const name = document.createElement('span');
        name.className = 'group-name';
        name.textContent = group.name;
        const restore = document.createElement('button');
        restore.className = 'close-btn restore-btn';
        restore.title = 'Restore group';
        restore.setAttribute('aria-label', 'Restore group');
        restore.innerHTML = '<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 7 5 3 9 7"/><path d="M5 3v6a4 4 0 0 0 4 4h2"/></svg>';
        restore.onclick = e => { e.stopPropagation(); emitWithCsrf('group_restore', { group_id: group.id }); };
        const body = document.createElement('div');
        body.className = 'group-body' + (expandedArchivedGroups.has(group.id) ? ' expanded' : '');
        header.onclick = () => {
            const expanded = body.classList.toggle('expanded');
            chevron.classList.toggle('expanded', expanded);
            if (expanded) expandedArchivedGroups.add(group.id);
            else expandedArchivedGroups.delete(group.id);
        };
        header.append(chevron, name, restore);
        wrapper.append(header, body);
        list.appendChild(wrapper);
        groupBodies.set(group.id, body);
        (group.archived_members || []).forEach(key => {
            if ((data.session_groups || {})[key] !== group.id) return;
            groupedMembers.add(key);
            if (key.startsWith('chat:')) return;
            const item = document.createElement('div');
            item.className = 'session-item archived-item';
            item.dataset.session = key;
            const label = document.createElement('span');
            label.className = 'session-name';
            label.textContent = key;
            item.appendChild(label);
            body.appendChild(item);
        });
    });
    (data.sessions || []).forEach(s => {
        const item = document.createElement('div');
        item.className = 'session-item archived-item';
        item.dataset.session = 'chat:' + s.id;
        const name = document.createElement('span');
        name.className = 'session-name';
        name.textContent = s.name;
        const btns = document.createElement('span');
        btns.style.cssText = 'display:flex;gap:4px;flex-shrink:0';
        const restoreBtn = document.createElement('button');
        restoreBtn.className = 'close-btn restore-btn';
        restoreBtn.title = 'Restore';
        restoreBtn.setAttribute('aria-label', 'Restore');
        restoreBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 7 5 3 9 7"/><path d="M5 3v6a4 4 0 0 0 4 4h2"/></svg>';
        restoreBtn.onclick = (e) => { e.stopPropagation(); emitWithCsrf('acp_restore', { session_id: s.id }); };
        const delBtn = document.createElement('button');
        delBtn.className = 'close-btn';
        delBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
        delBtn.title = 'Delete';
        delBtn.onclick = (e) => { e.stopPropagation(); showConfirm('Delete "' + s.name + '" permanently?').then(ok => { if (ok) { item.remove(); emitWithCsrf('acp_delete_archived', { session_id: s.id }); } }); };
        btns.appendChild(restoreBtn);
        btns.appendChild(delBtn);
        item.appendChild(name);
        item.appendChild(btns);
        item.addEventListener('click', () => {
            openChatPane(s.id);
            if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
        });
        const groupBody = groupedMembers.has(item.dataset.session) && groupBodies.get((data.session_groups || {})[item.dataset.session]);
        (groupBody || list).appendChild(item);
    });
    filterArchived();
});

socket.on('acp_restored', (data) => {
    if (data.ok) {
        emitWithCsrf('acp_list_archived');
        // Force reload if already previewing this chat
        for (const pn of [1, 2]) {
            const iframe = document.getElementById(`browser${pn}`).querySelector('iframe');
            if (iframe && iframe.src.includes('/chat/' + data.session_id)) {
                iframe.parentElement.innerHTML = '';
            }
        }
        openChatPane(data.session_id);
    }
});
