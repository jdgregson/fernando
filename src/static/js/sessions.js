// --- State ---
let isSplit = false;
let activeTerminal = 1;
let paneTypes = { 1: 'terminal', 2: 'terminal' };
let currentSession1 = null;
let currentSession2 = null;

// --- Foreground / Reconnect ---
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) handleForeground();
});
window.addEventListener('pageshow', (event) => {
    if (event.persisted) handleForeground();
});

let _fgDebounce = null;
function handleForeground() {
    clearTimeout(_fgDebounce);
    _fgDebounce = setTimeout(_doForeground, 50);
}
function _doForeground() {
    if (!socket.connected) { socket.connect(); return; }
    if (currentSession1 && paneTypes[1] === 'terminal') {
        setTimeout(() => {
            _paneSession[1] = currentSession1;
            showTermInPane(currentSession1, 1);
            emitWithCsrf('attach_session', { terminal: 1, session: currentSession1, skip_replay: true });
            setTimeout(doFit, 100);
        }, 200);
    }
    if (currentSession2 && paneTypes[2] === 'terminal' && isSplit) {
        setTimeout(() => {
            _paneSession[2] = currentSession2;
            showTermInPane(currentSession2, 2);
            emitWithCsrf('attach_session', { terminal: 2, session: currentSession2, skip_replay: true });
            setTimeout(doFit, 100);
        }, 200);
    }
    setTimeout(() => {
        const activeTerm = activeTerminal === 1 ? term1 : term2;
        if (paneTypes[activeTerminal] === 'terminal' && activeTerm) activeTerm.focus();
        // iOS: re-toggle spacers after returning from background
        if (/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
            document.querySelectorAll('.ios-spacer').forEach(s => {
                s.style.display = 'none';
                setTimeout(() => { s.style.display = ''; }, 100);
            });
        }
    }, 400);
}

// Called from core.js on socket 'connected'
function onSocketConnected() {
    if (window._urlParamsProcessed) { handleForeground(); return; }
    const params = new URLSearchParams(window.location.search);
    const urlSession = params.get('session');
    const urlSession2 = params.get('session2');
    const urlSplit = params.get('split') === '1';
    if (!currentSession1 && paneTypes[1] !== 'browser') {
        if (urlSession && urlSession.startsWith('chat:')) {
            openChatPane(urlSession.slice(5));
        } else if (urlSession === 'desktop') {
            toggleDesktop();
        } else if (urlSession && urlSession.startsWith('notebook:')) {
            openNotebook(urlSession.slice(9));
        } else if (urlSession) {
            attachSession(urlSession);
        } else {
            openNewSessionModal();
        }
        if (urlSplit) {
            if (!isSplit) toggleSplit();
            if (urlSession2 && urlSession2.startsWith('chat:')) {
                openChatPane(urlSession2.slice(5));
            } else if (urlSession2 === 'desktop') {
                toggleDesktop();
            } else if (urlSession2 && urlSession2.startsWith('notebook:')) {
                openNotebook(urlSession2.slice(9));
            } else if (urlSession2) {
                attachSession(urlSession2);
            }
            const urlActive = parseInt(params.get('active'));
            if (urlActive === 1) setActiveTerminal(1);
            const activeSession = urlActive === 1 ? urlSession : urlSession2;
            if (activeSession) highlightSidebarItem(activeSession);
        }
        window._urlParamsProcessed = true;
    } else if (currentSession1) {
        handleForeground();
    }
}

// --- Desktop ---
function ensureDesktopIframe(browser) {
    let iframe = browser.querySelector('iframe');
    if (!iframe) {
        iframe = document.createElement('iframe');
        iframe.src = '/kasm/?resize=remote&api_key=' + encodeURIComponent(window.FERNANDO_API_KEY) + '#show_control_bar=1';
        iframe.allow = 'autoplay; clipboard-read; clipboard-write';
        iframe.setAttribute('allowfullscreen', '');
        iframe.setAttribute('webkitallowfullscreen', '');
        iframe.setAttribute('mozallowfullscreen', '');
        browser.appendChild(iframe);
    }
    return iframe;
}

function toggleKasmKeyboard() {
    for (const pn of [1, 2]) {
        if (paneTypes[pn] !== 'browser') continue;
        const iframe = document.getElementById('browser' + pn).querySelector('iframe');
        if (!iframe || !iframe.src.includes('/kasm/')) continue;
        try {
            const btn = iframe.contentDocument.getElementById('noVNC_keyboard_button');
            if (btn) { btn.click(); return; }
        } catch(e) {}
    }
}

function updateKbdBtn() {
    const hasDesktop = [1, 2].some(pn => {
        if (paneTypes[pn] !== 'browser') return false;
        const iframe = document.getElementById('browser' + pn).querySelector('iframe');
        return iframe && iframe.src.includes('/kasm/');
    });
    document.getElementById('kbdBtn').classList.toggle('kbdVisible', hasDesktop);
    const hasTerminal = paneTypes[activeTerminal] === 'terminal';
    document.getElementById('resizeBtn').classList.toggle('resizeBtnVisible', hasTerminal);
    updateMobileControls();
}

function toggleDesktop() {
    const activePane = activeTerminal;
    const browser = document.getElementById(`browser${activePane}`);
    const terminal = document.getElementById(`terminal${activePane}`);
    const currentIframe = browser.querySelector('iframe');
    const isDesktop = paneTypes[activePane] === 'browser' && currentIframe && currentIframe.src.includes('/kasm/');
    if (isDesktop) {
        paneTypes[activePane] = 'terminal';
        paneNotebook[activePane] = null;
        terminal.classList.remove('hidden');
        browser.classList.add('hidden');
        setTimeout(doFit, 100);
    } else {
        paneTypes[activePane] = 'browser';
        paneNotebook[activePane] = null;
        terminal.classList.add('hidden');
        browser.classList.remove('hidden');
        if (activePane === 1) currentSession1 = null;
        else currentSession2 = null;
        browser.innerHTML = '';
        ensureDesktopIframe(browser);
    }
    highlightSidebarItem('desktop');
    syncUrlParams();
    updateKbdBtn();
}

// --- Notes (Notebooks) ---
// Track which notebook is open in each pane: paneNotebook[1] = 'default', etc.
let paneNotebook = { 1: null, 2: null };

function ensureNotebookIframe(browser, notebook) {
    browser.innerHTML = '';
    browser.style.background = '#0d2848';
    const iframe = document.createElement('iframe');
    iframe.src = `/notes/${encodeURIComponent(notebook)}/?api_key=` + encodeURIComponent(window.FERNANDO_API_KEY);
    iframe.style.cssText = 'width:100%;height:100%;border:none;background:#0d2848';
    iframe.allow = 'storage-access';
    browser.appendChild(iframe);
    return iframe;
}

function openNotebook(notebook) {
    const activePane = activeTerminal;
    const browser = document.getElementById(`browser${activePane}`);
    const terminal = document.getElementById(`terminal${activePane}`);
    paneTypes[activePane] = 'browser';
    paneNotebook[activePane] = notebook;
    terminal.classList.add('hidden');
    browser.classList.remove('hidden');
    if (activePane === 1) currentSession1 = null;
    else currentSession2 = null;
    browser.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#5a9fd4;font-family:sans-serif">Starting notebook...</div>';
    highlightSidebarItem('notebook:' + notebook);
    syncUrlParams();
    updateKbdBtn();
    // Start the container (if already running, backend returns immediately)
    emitWithCsrf('start_notebook', { name: notebook });
    emitWithCsrf('get_sessions');
}

function showNotebookPicker() {
    closeNewSessionModal();
    document.getElementById('notebookPickerModal').classList.add('open');
    emitWithCsrf('list_notebooks');
}

function closeNotebookPicker() {
    document.getElementById('notebookPickerModal').classList.remove('open');
}

function openSelectedNotebook() {
    const sel = document.getElementById('notebookSelect');
    const name = sel.value;
    if (!name) return;
    closeNotebookPicker();
    openNotebook(name);
}

function promptCreateNotebook() {
    document.getElementById('notebookPickerModal').classList.remove('open');
    const modal = document.getElementById('notebookCreateModal');
    const input = document.getElementById('notebookNameInput');
    input.value = '';
    modal.classList.add('open');
    input.focus();
}

function closeCreateNotebook() {
    document.getElementById('notebookCreateModal').classList.remove('open');
}

let pendingNotebookOpen = null;

function submitCreateNotebook() {
    const name = document.getElementById('notebookNameInput').value.trim().toLowerCase();
    if (!name) return;
    pendingNotebookOpen = name;
    emitWithCsrf('create_notebook', { name: name });
    closeCreateNotebook();
}

function deleteSelectedNotebook() {
    const sel = document.getElementById('notebookSelect');
    const name = sel.value;
    if (!name) return;
    showConfirm('Delete notebook "' + name + '"? This cannot be undone.').then(confirmed => {
        if (!confirmed) return;
        emitWithCsrf('delete_notebook', { name: name });
    });
}

socket.on('notebook_deleted', (data) => {
    emitWithCsrf('list_notebooks');
    for (const pn of [1, 2]) {
        if (paneNotebook[pn] === data.name) {
            paneNotebook[pn] = null;
            document.getElementById(`browser${pn}`).innerHTML = '';
        }
    }
});

socket.on('notebooks_list', (data) => {
    const sel = document.getElementById('notebookSelect');
    sel.innerHTML = '';
    (data.notebooks || []).forEach(nb => {
        const opt = document.createElement('option');
        opt.value = nb.name;
        opt.textContent = nb.name + (nb.running ? ' (running)' : '');
        sel.appendChild(opt);
    });
    if (sel.options.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No notebooks — create one';
        sel.appendChild(opt);
    }
});

socket.on('notebook_created', (data) => {
    emitWithCsrf('list_notebooks');
    const name = data.notebook && data.notebook.name;
    if (pendingNotebookOpen && name === pendingNotebookOpen) {
        pendingNotebookOpen = null;
        openNotebook(name);
    }
});

socket.on('notebook_started', (data) => {
    // Find the pane waiting for this notebook and load the iframe
    for (const pn of [1, 2]) {
        if (paneNotebook[pn] === data.name && paneTypes[pn] === 'browser') {
            const browser = document.getElementById(`browser${pn}`);
            ensureNotebookIframe(browser, data.name);
            break;
        }
    }
    highlightSidebarItem('notebook:' + data.name);
    syncUrlParams();
});

socket.on('notebook_error', (data) => {
    showAlert('Notebook error: ' + data.error);
    // Revert pane if it was waiting
    for (const pn of [1, 2]) {
        if (paneTypes[pn] === 'browser' && paneNotebook[pn]) {
            const browser = document.getElementById(`browser${pn}`);
            if (browser.querySelector('iframe') === null) {
                paneTypes[pn] = 'terminal';
                paneNotebook[pn] = null;
                const terminal = document.getElementById(`terminal${pn}`);
                terminal.classList.remove('hidden');
                browser.classList.add('hidden');
            }
        }
    }
});

function restartDesktop() {
    showConfirm('Restart the desktop container? This will kill all running desktop applications.').then(confirmed => {
        if (!confirmed) return;
        [1, 2].forEach(n => {
            const b = document.getElementById(`browser${n}`);
            if (b) b.innerHTML = '';
        });
        emitWithCsrf('restart_desktop');
    });
}

socket.on('desktop_restart_error', (data) => { showAlert('Error: ' + data.error); });
socket.on('desktop_restarted', () => {
    [1, 2].forEach(n => {
        if (paneTypes[n] === 'browser') {
            const b = document.getElementById(`browser${n}`);
            if (b) { b.innerHTML = ''; ensureDesktopIframe(b); }
        }
    });
});

function setPaneType(paneNum, type) {
    paneTypes[paneNum] = type;
    const terminal = document.getElementById(`terminal${paneNum}`);
    const browser = document.getElementById(`browser${paneNum}`);
    if (type === 'terminal') {
        terminal.classList.remove('hidden');
        browser.classList.add('hidden');
        setTimeout(doFit, 100);
    } else {
        terminal.classList.add('hidden');
        browser.classList.remove('hidden');
        ensureDesktopIframe(browser);
    }
    updateKbdBtn();
}

// --- Session List ---
function highlightSidebarItem(sessionKey) {
    document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
    const item = document.querySelector(`.session-item[data-session="${sessionKey}"]`);
    if (item) item.classList.add('active');
}

let sessionListInitialized = false;
let lastSessionsKey = '';
function updateSessionList(sessions, chatSessions, data) {
    const sessionList = document.getElementById('sessionList');

    if (!currentSession1 && paneTypes[1] !== 'browser' && !window._urlParamsProcessed) {
        const params = new URLSearchParams(window.location.search);
        const urlSession = params.get('session');
        const urlSession2 = params.get('session2');
        const urlSplit = params.get('split') === '1';
        if (urlSession && urlSession.startsWith('chat:')) {
            openChatPane(urlSession.slice(5));
        } else if (urlSession === 'desktop') {
            toggleDesktop();
        } else if (urlSession && urlSession.startsWith('notebook:')) {
            openNotebook(urlSession.slice(9));
        } else if (urlSession && sessions.includes(urlSession)) {
            attachSession(urlSession);
        } else if (sessions.length > 0) {
            const saved = sessionStorage.getItem('fernando_session1');
            attachSession(saved && sessions.includes(saved) ? saved : sessions[0]);
        }
        if (urlSplit) {
            if (!isSplit) toggleSplit();
            if (urlSession2 && urlSession2.startsWith('chat:')) {
                openChatPane(urlSession2.slice(5));
            } else if (urlSession2 === 'desktop') {
                toggleDesktop();
            } else if (urlSession2 && urlSession2.startsWith('notebook:')) {
                openNotebook(urlSession2.slice(9));
            } else if (urlSession2 && sessions.includes(urlSession2)) {
                attachSession(urlSession2);
            }
            const urlActive = parseInt(params.get('active'));
            if (urlActive === 1) setActiveTerminal(1);
            const activeSession = urlActive === 1 ? urlSession : urlSession2;
            if (activeSession) highlightSidebarItem(activeSession);
        }
        window._urlParamsProcessed = true;
    }

    const chatKeys = chatSessions.map(c => 'chat:' + c.id + ':' + c.name);
    const newKey = JSON.stringify([...sessions].sort()) + '|' + JSON.stringify(chatKeys.sort()) + '|' + JSON.stringify((data.running_notebooks || []).sort());
    if (sessionListInitialized && lastSessionsKey === newKey) return;
    console.log('[sidebar-rebuild]', {sessions, chatSessions, notebooks: data.running_notebooks, oldKey: lastSessionsKey, newKey});
    sessionListInitialized = true;
    lastSessionsKey = newKey;

    sessionList.innerHTML = '';

    // Desktop item
    const desktopItem = document.createElement('div');
    desktopItem.className = 'session-item';
    desktopItem.dataset.session = 'desktop';
    const desktopName = document.createElement('span');
    desktopName.className = 'session-name';
    desktopName.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px"><rect x="1" y="2" width="14" height="10" rx="1"/><line x1="5" y1="14" x2="11" y2="14"/><line x1="8" y1="12" x2="8" y2="14"/></svg>Desktop';
    const restartBtn = document.createElement('button');
    restartBtn.className = 'close-btn';
    restartBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 8a6 6 0 0 1 10.3-4.1"/><path d="M14 8a6 6 0 0 1-10.3 4.1"/><polyline points="2 2 2 6 6 6"/><polyline points="14 14 14 10 10 10"/></svg>';
    restartBtn.onclick = (e) => { e.stopPropagation(); restartDesktop(); };
    desktopItem.appendChild(desktopName);
    desktopItem.appendChild(restartBtn);
    desktopItem.addEventListener('click', function() {
        toggleDesktop();
        if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
    });
    sessionList.appendChild(desktopItem);

    // Notebook items (running containers)
    const runningNotebooks = data.running_notebooks || [];
    runningNotebooks.forEach(nb => {
        const nbItem = document.createElement('div');
        nbItem.className = 'session-item';
        nbItem.dataset.session = 'notebook:' + nb;
        const nbName = document.createElement('span');
        nbName.className = 'session-name';
        nbName.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-1px;margin-right:4px"><rect x="3" y="1" width="10" height="14" rx="1"/><line x1="6" y1="1" x2="6" y2="15"/><line x1="1" y1="4" x2="3" y2="4"/><line x1="1" y1="8" x2="3" y2="8"/><line x1="1" y1="12" x2="3" y2="12"/></svg>' + nb;
        const closeBtn = document.createElement('button');
        closeBtn.className = 'close-btn';
        closeBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
        closeBtn.onclick = (e) => {
            e.stopPropagation();
            // Close the pane and stop the container
            for (const pn of [1, 2]) {
                if (paneNotebook[pn] === nb) {
                    paneTypes[pn] = 'terminal';
                    paneNotebook[pn] = null;
                    const terminal = document.getElementById(`terminal${pn}`);
                    const browser = document.getElementById(`browser${pn}`);
                    terminal.classList.remove('hidden');
                    browser.classList.add('hidden');
                    browser.innerHTML = '';
                    setTimeout(doFit, 100);
                }
            }
            emitWithCsrf('stop_notebook', { name: nb });
            emitWithCsrf('get_sessions');
            syncUrlParams();
            updateKbdBtn();
        };
        nbItem.appendChild(nbName);
        nbItem.appendChild(closeBtn);
        nbItem.addEventListener('click', function() {
            openNotebook(nb);
            if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
        });
        sessionList.appendChild(nbItem);
    });

    // Terminal sessions
    sessions.forEach(session => {
        const item = document.createElement('div');
        item.className = 'session-item';
        item.dataset.session = session;
        const nameSpan = document.createElement('span');
        nameSpan.className = 'session-name';
        const sIcon = session.startsWith('Shell') ? '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px"><polyline points="2 4 6 8 2 12"/><line x1="8" y1="12" x2="14" y2="12"/></svg>'
            : session.startsWith('Kiro') ? '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px"><circle cx="8" cy="5" r="3"/><path d="M3 14c0-3 2-5 5-5s5 2 5 5"/></svg>'
            : '';
        nameSpan.innerHTML = sIcon + session;
        const closeBtn = document.createElement('button');
        closeBtn.className = 'close-btn';
        closeBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
        closeBtn.onclick = (e) => closeSession(e, session);
        item.appendChild(nameSpan);
        item.appendChild(closeBtn);

        let clickTimer = null;
        function startRename() {
            const oldName = nameSpan.textContent;
            const input = document.createElement('input');
            input.value = oldName;
            input.style.cssText = 'background:#3c3c3c;color:#fff;border:1px solid #3465a3;outline:none;padding:2px 4px;font-family:monospace;font-size:inherit;width:calc(100% - 32px)';
            nameSpan.replaceWith(input);
            input.focus();
            input.select();
            function commit() {
                if (!input.parentNode) return;
                const newName = input.value.trim();
                input.replaceWith(nameSpan);
                if (newName && newName !== oldName) emitWithCsrf('rename_session', { old_name: oldName, new_name: newName });
            }
            input.addEventListener('keydown', function(ev) {
                ev.stopPropagation();
                if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
                if (ev.key === 'Escape') input.replaceWith(nameSpan);
            });
            input.addEventListener('blur', commit);
            input.addEventListener('click', function(ev) { ev.stopPropagation(); });
        }
        item.addEventListener('click', function(e) {
            if (e.detail === 2) { clearTimeout(clickTimer); e.stopPropagation(); startRename(); return; }
            clickTimer = setTimeout(() => {
                attachSession(this.dataset.session);
                if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
            }, 250);
        });
        let holdTimer = null;
        nameSpan.addEventListener('touchstart', function(e) {
            holdTimer = setTimeout(() => { e.preventDefault(); holdTimer = 'fired'; startRename(); }, 500);
        }, {passive: false});
        nameSpan.addEventListener('touchend', function(e) {
            if (holdTimer === 'fired') e.preventDefault(); else clearTimeout(holdTimer);
            holdTimer = null;
        });
        nameSpan.addEventListener('touchmove', function() { if (holdTimer !== 'fired') clearTimeout(holdTimer); });
        sessionList.appendChild(item);
    });

    // Chat sessions
    chatSessions.forEach(chat => {
        const chatId = chat.id;
        const item = document.createElement('div');
        item.className = 'session-item';
        item.dataset.session = 'chat:' + chatId;
        const nameSpan = document.createElement('span');
        nameSpan.className = 'session-name';
        nameSpan.innerHTML = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px"><path d="M2 3h12v8H6l-4 3V3z"/></svg>' + chat.name;
        const closeBtn = document.createElement('button');
        closeBtn.className = 'close-btn';
        closeBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
        closeBtn.onclick = (e) => { e.stopPropagation(); closeChatSession(chatId); };
        item.appendChild(nameSpan);
        item.appendChild(closeBtn);

        let clickTimer = null;
        function startChatRename() {
            const oldName = nameSpan.textContent;
            const inp = document.createElement('input');
            inp.value = oldName;
            inp.style.cssText = 'background:#3c3c3c;color:#fff;border:1px solid #3465a3;outline:none;padding:2px 4px;font-family:monospace;font-size:inherit;width:calc(100% - 32px)';
            nameSpan.replaceWith(inp);
            inp.focus();
            inp.select();
            function commit() {
                if (!inp.parentNode) return;
                const newName = inp.value.trim();
                inp.replaceWith(nameSpan);
                if (newName && newName !== oldName) {
                    nameSpan.textContent = newName;
                    emitWithCsrf('acp_rename', { session_id: chatId, name: newName });
                }
            }
            inp.addEventListener('keydown', function(ev) {
                ev.stopPropagation();
                if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
                if (ev.key === 'Escape') inp.replaceWith(nameSpan);
            });
            inp.addEventListener('blur', commit);
            inp.addEventListener('click', function(ev) { ev.stopPropagation(); });
        }
        item.addEventListener('click', function(e) {
            if (e.detail === 2) { clearTimeout(clickTimer); e.stopPropagation(); startChatRename(); return; }
            clickTimer = setTimeout(() => {
                openChatPane(chatId);
                if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
            }, 250);
        });
        let holdTimer = null;
        nameSpan.addEventListener('touchstart', function(e) {
            holdTimer = setTimeout(() => { e.preventDefault(); holdTimer = 'fired'; startChatRename(); }, 500);
        }, {passive: false});
        nameSpan.addEventListener('touchend', function(e) {
            if (holdTimer === 'fired') e.preventDefault(); else clearTimeout(holdTimer);
            holdTimer = null;
        });
        nameSpan.addEventListener('touchmove', function() { if (holdTimer !== 'fired') clearTimeout(holdTimer); });
        sessionList.appendChild(item);
    });

    // Re-highlight
    const hp = isSplit ? activeTerminal : 1;
    if (paneTypes[hp] === 'browser') {
        highlightSidebarItem(getBrowserPaneSession(hp));
    } else if (hp === 1 ? currentSession1 : currentSession2) {
        highlightSidebarItem(hp === 1 ? currentSession1 : currentSession2);
    }
    if (showArchived) {
        document.querySelectorAll('#sessionList > .session-item:not(.archived-item)').forEach(el => el.style.display = 'none');
        emitWithCsrf('acp_list_archived');
    }
}

socket.on('sessions_list', data => { updateSessionList(data.sessions, data.chat_sessions || [], data); });
setInterval(() => { emitWithCsrf('get_sessions'); }, 2000);

socket.on('session_created', data => {
    emitWithCsrf('get_sessions');
    if (data.switch && data.name) attachSession(data.name);
});
socket.on('session_renamed', data => {
    if (currentSession1 === data.old_name) currentSession1 = data.new_name;
    if (currentSession2 === data.old_name) currentSession2 = data.new_name;
    emitWithCsrf('get_sessions');
    syncUrlParams();
});
socket.on('session_closed', (data) => {
    if (data && data.session) destroyTerm(data.session);
    emitWithCsrf('get_sessions');
});

// --- Attach / Detach ---
function attachSession(sessionName) {
    if (sessionName === 'desktop') { toggleDesktop(); return; }
    if (sessionName.startsWith('notebook:')) { openNotebook(sessionName.slice(9)); return; }
    // Already attached to this pane — no-op.
    // In split mode, if the user recently directly clicked a different pane,
    // honor that as the target (handles race where activeTerminal hasn't updated).
    const currentSession = activeTerminal === 1 ? currentSession1 : currentSession2;
    if (paneTypes[activeTerminal] === 'terminal' && currentSession === sessionName) {
        if (isSplit && _lastDirectPaneTarget !== activeTerminal
            && Date.now() - _lastDirectPaneTouch < 5000) {
            setActiveTerminal(_lastDirectPaneTarget, true);
        } else {
            highlightSidebarItem(sessionName);
            return;
        }
    }
    if (paneTypes[activeTerminal] === 'browser') {
        const browser = document.getElementById(`browser${activeTerminal}`);
        const terminal = document.getElementById(`terminal${activeTerminal}`);
        paneTypes[activeTerminal] = 'terminal';
        paneNotebook[activeTerminal] = null;
        terminal.classList.remove('hidden');
        browser.classList.add('hidden');
    }
    updateKbdBtn();
    if (activeTerminal === 1) { currentSession1 = sessionName; sessionStorage.setItem('fernando_session1', sessionName); }
    else { currentSession2 = sessionName; sessionStorage.setItem('fernando_session2', sessionName); }
    _paneSession[activeTerminal] = sessionName;
    // If this session was in the other pane, detach the stale viewer to prevent
    // duplicate output (the PTY broadcasts to all viewers on a session).
    const otherPane = activeTerminal === 1 ? 2 : 1;
    if (_paneSession[otherPane] === sessionName) {
        emitWithCsrf('detach_viewer', { terminal: otherPane });
        _paneSession[otherPane] = null;
        if (otherPane === 1) { currentSession1 = null; sessionStorage.removeItem('fernando_session1'); }
        else { currentSession2 = null; sessionStorage.removeItem('fernando_session2'); }
    }
    const entry = showTermInPane(sessionName, activeTerminal);
    // If this session already has a rendered terminal, skip scrollback replay —
    // the content is already in the DOM. We still attach to get live output.
    const skipReplay = entry.ready && !entry.firstAttach;
    entry.firstAttach = false;
    emitWithCsrf('attach_session', { terminal: activeTerminal, session: sessionName, skip_replay: skipReplay });
    highlightSidebarItem(sessionName);
    if (entry.ready) entry.wterm.focus();
    setTimeout(doFit, 100);
    syncUrlParams();
}

function getBrowserPaneSession(pane) {
    const iframe = document.querySelector(`#browser${pane} iframe`);
    if (iframe && iframe.src) {
        const m = iframe.src.match(/\/chat\/([^/?#]+)/);
        if (m) return 'chat:' + m[1];
        const nb = iframe.src.match(/\/notes\/([^/?#]+)\//);
        if (nb) return 'notebook:' + nb[1];
    }
    if (paneNotebook[pane]) return 'notebook:' + paneNotebook[pane];
    return 'desktop';
}

function syncUrlParams() {
    if (!window._urlParamsProcessed) return;
    const params = new URLSearchParams();
    const s1 = paneTypes[1] === 'browser' ? getBrowserPaneSession(1) : currentSession1;
    const s2 = paneTypes[2] === 'browser' ? getBrowserPaneSession(2) : currentSession2;
    if (s1) params.set('session', s1);
    if (isSplit && s2) { params.set('session2', s2); params.set('split', '1'); params.set('active', String(activeTerminal)); }
    const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    try { history.replaceState(null, '', newUrl); } catch(e) {}
}

// --- Split ---
function toggleSplit() {
    isSplit = !isSplit;
    const container2 = document.getElementById('terminal2-container');
    if (isSplit) {
        document.getElementById('terminal1-container').classList.remove('hidden');
        document.getElementById('terminal2-container').classList.remove('hidden');
        setActiveTerminal(2, true);
    } else {
        const keep = activeTerminal;
        const discard = keep === 1 ? 2 : 1;
        document.getElementById(`terminal${discard}-container`).classList.add('hidden');
        // Detach the discarded pane's viewer so it doesn't produce duplicate output
        emitWithCsrf('detach_viewer', { terminal: discard });
        _paneSession[discard] = null;
        setActiveTerminal(keep, true);
    }
    setTimeout(doFit, 100);
    syncUrlParams();
}

// Focus guard: only direct user touch/click can change the active pane.
// postMessage from iframes and setInterval polling cannot override a
// user's direct pane selection.
let _lastDirectPaneTouch = 0;
let _lastDirectPaneTarget = 0;

function setActiveTerminal(termNum, direct) {
    if (direct) {
        _lastDirectPaneTouch = Date.now();
        _lastDirectPaneTarget = termNum;
    } else {
        // Indirect call — block if user recently directly touched a different pane
        if (Date.now() - _lastDirectPaneTouch < 2000 && _lastDirectPaneTarget !== termNum) return;
    }
    activeTerminal = termNum;
    // Blur the other pane's textarea so next tap triggers a fresh focus event
    // (which drives scroll-into-view via setupFocusScroll)
    const otherPane = termNum === 1 ? 2 : 1;
    const otherTerm = otherPane === 1 ? (typeof term1 !== 'undefined' ? term1 : null) : (typeof term2 !== 'undefined' ? term2 : null);
    if (otherTerm && otherTerm.element) {
        const ta = otherTerm.element.querySelector('textarea');
        if (ta) ta.blur();
    }
    const c1 = document.getElementById('terminal1-container');
    const c2 = document.getElementById('terminal2-container');
    c1.classList.toggle('active', termNum === 1);
    c2.classList.toggle('active', termNum === 2);
    if (isSplit) { c1.classList.add('split-mode'); c2.classList.add('split-mode'); }
    else { c1.classList.remove('split-mode'); c2.classList.remove('split-mode'); }
    // Don't auto-focus textarea here — wterm's own click handler does it
    // after checking for text selection. Focusing here would kill selection.
    // Scroll the pane into view on direct touch (mobile split mode)
    if (direct && typeof isSplit !== 'undefined' && isSplit) {
        setTimeout(() => {
            const container = document.getElementById('terminal' + termNum + '-container');
            const rect = container.getBoundingClientRect();
            window.scrollBy({ top: rect.top - 2, behavior: 'smooth' });
        }, 300);
    }
    updateKbdBtn();
    updateMobileControls();
    syncUrlParams();
}

// Click handlers
function syncPaneSidebar(paneNum) {
    const s = paneTypes[paneNum] === 'browser' ? getBrowserPaneSession(paneNum) : (paneNum === 1 ? currentSession1 : currentSession2);
    if (s) highlightSidebarItem(s);
    else document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
}
function activatePane1() {
    setActiveTerminal(1, true);
    syncPaneSidebar(1);
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
}
function activatePane2() {
    if (isSplit) {
        setActiveTerminal(2, true);
        syncPaneSidebar(2);
    }
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
}
document.getElementById('terminal1-container').addEventListener('mousedown', activatePane1);
document.getElementById('terminal1-container').addEventListener('touchstart', activatePane1, { passive: true });
document.getElementById('terminal2-container').addEventListener('mousedown', activatePane2);
document.getElementById('terminal2-container').addEventListener('touchstart', activatePane2, { passive: true });

// iframe focus detection handled by container touchstart/mousedown handlers

// Handle focus from iframes (notes, desktop) via postMessage
window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'notes-focus') {
        for (const pn of [1, 2]) {
            const browser = document.getElementById('browser' + pn);
            if (!browser) continue;
            const iframe = browser.querySelector('iframe');
            if (!iframe) continue;
            try {
                if (iframe.contentWindow === e.source) {
                    if (pn === 1) activatePane1();
                    else activatePane2();
                    return;
                }
            } catch(ex) {}
        }
        // Fallback: if source matching failed, activate whichever pane has a notebook
        for (const pn of [1, 2]) {
            if (paneNotebook[pn] && activeTerminal !== pn) {
                if (pn === 1) activatePane1();
                else activatePane2();
                return;
            }
        }
    }
});

// --- New Session Modal ---
function openNewSessionModal() {
    document.getElementById('newSessionModal').classList.add('open');
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
}
function closeNewSessionModal() { document.getElementById('newSessionModal').classList.remove('open'); }
function createSessionType(type) { emitWithCsrf('create_session', { type: type }); closeNewSessionModal(); }
function closeSession(event, sessionName) {
    event.stopPropagation();
    showConfirm(`Close session "${sessionName}"?`).then(result => {
        if (result) emitWithCsrf('close_session', { session: sessionName });
    });
}
function toggleSidebar() { document.getElementById('sidebar').classList.toggle('open'); }

// Close sidebar on outside click (mobile)
document.addEventListener('click', (e) => {
    if (window.innerWidth <= 500) {
        const sidebar = document.getElementById('sidebar');
        const sidebarToggle = document.querySelector('.sidebar-toggle');
        if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== sidebarToggle) {
            sidebar.classList.remove('open');
        }
    }
});

// --- Initial load ---
emitWithCsrf('get_sessions');
