// --- ACP Chat Sessions ---
function createChatSession() {
    closeNewSessionModal();
    emitWithCsrf('acp_create');
}

socket.on('acp_created', (data) => { openChatPane(data.session_id); });

function openChatPane(chatId) {
    const pane = activeTerminal;
    const browser = document.getElementById(`browser${pane}`);
    const terminal = document.getElementById(`terminal${pane}`);
    paneTypes[pane] = 'browser';
    terminal.classList.add('hidden');
    browser.classList.remove('hidden');
    const existing = browser.querySelector('iframe');
    if (!existing || !existing.src.includes('/chat/' + chatId)) {
        browser.innerHTML = '';
        const iframe = document.createElement('iframe');
        iframe.src = '/chat/' + chatId + '?api_key=' + encodeURIComponent(window.FERNANDO_API_KEY);
        iframe.style.cssText = 'width:100%;height:100%;border:none';
        browser.appendChild(iframe);
    }
    if (pane === 1) currentSession1 = null;
    else currentSession2 = null;
    highlightSidebarItem('chat:' + chatId);
    updateKbdBtn();
    syncUrlParams();
}

function closeChatSession(chatId) {
    emitWithCsrf('acp_close', { session_id: chatId });
    [1, 2].forEach(pane => {
        const browser = document.getElementById(`browser${pane}`);
        const iframe = browser.querySelector('iframe');
        if (iframe && iframe.src.includes('/chat/' + chatId)) {
            paneTypes[pane] = 'terminal';
            document.getElementById(`terminal${pane}`).classList.remove('hidden');
            browser.classList.add('hidden');
            browser.innerHTML = '';
            setTimeout(doFit, 100);
        }
    });
    updateKbdBtn();
}

// --- Archived ---
let showArchived = false;
function toggleArchivedInline() {
    showArchived = !showArchived;
    document.getElementById('switchLabelActive').classList.toggle('active', !showArchived);
    document.getElementById('switchLabelArchived').classList.toggle('active', showArchived);
    const search = document.getElementById('archiveSearch');
    search.style.display = showArchived ? '' : 'none';
    if (!showArchived) search.value = '';
    document.querySelectorAll('#sessionList > .session-item:not(.archived-item)').forEach(el => el.style.display = showArchived ? 'none' : '');
    if (showArchived) emitWithCsrf('acp_list_archived');
    else document.querySelectorAll('.archived-item').forEach(el => el.remove());
}
function filterArchived() {
    const q = document.getElementById('archiveSearch').value.toLowerCase();
    document.querySelectorAll('.archived-item').forEach(el => {
        el.style.display = el.querySelector('.session-name').textContent.toLowerCase().includes(q) ? '' : 'none';
    });
}

socket.on('acp_archived_list', (data) => {
    document.querySelectorAll('.archived-item').forEach(el => el.remove());
    if (!showArchived) return;
    const list = document.getElementById('sessionList');
    (data.sessions || []).forEach(s => {
        const item = document.createElement('div');
        item.className = 'session-item archived-item';
        const name = document.createElement('span');
        name.className = 'session-name';
        name.textContent = s.name;
        const btns = document.createElement('span');
        btns.style.cssText = 'display:flex;gap:4px;flex-shrink:0';
        const restoreBtn = document.createElement('button');
        restoreBtn.className = 'close-btn restore-btn';
        restoreBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 7 5 3 9 7"/><path d="M5 3v6a4 4 0 0 0 4 4h2"/></svg>';
        restoreBtn.onclick = (e) => { e.stopPropagation(); emitWithCsrf('acp_restore', { session_id: s.id }); };
        const delBtn = document.createElement('button');
        delBtn.className = 'close-btn';
        delBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
        delBtn.title = 'Delete';
        delBtn.onclick = (e) => { e.stopPropagation(); emitWithCsrf('acp_delete_archived', { session_id: s.id }); emitWithCsrf('acp_list_archived'); };
        btns.appendChild(restoreBtn);
        btns.appendChild(delBtn);
        item.appendChild(name);
        item.appendChild(btns);
        item.addEventListener('click', () => {
            openChatPane(s.id);
            if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
        });
        list.appendChild(item);
    });
});

socket.on('acp_restored', (data) => {
    if (data.ok) { emitWithCsrf('acp_list_archived'); openChatPane(data.session_id); }
});

// --- iframe messages ---
window.addEventListener('message', (e) => {

    if (e.data && e.data.type === 'acp-chat-focus') {
        for (const paneNum of [1, 2]) {
            if (paneTypes[paneNum] === 'browser') {
                const iframe = document.getElementById(`browser${paneNum}`).querySelector('iframe');
                if (iframe && iframe.contentWindow === e.source) { setActiveTerminal(paneNum); highlightSidebarItem('chat:' + e.data.sessionId); return; }
            }
        }
    }
    if (e.data && e.data.action === 'enable_audio') {
        for (const paneNum of [1, 2]) {
            if (paneTypes[paneNum] === 'browser') {
                const iframe = document.getElementById(`browser${paneNum}`).querySelector('iframe');
                if (iframe && iframe.contentWindow === e.source) { setActiveTerminal(paneNum); highlightSidebarItem('desktop'); return; }
            }
        }
    }
});
