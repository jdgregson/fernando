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

function handleForeground() {
    if (!socket.connected) socket.connect();
    if (currentSession1 && paneTypes[1] === 'terminal') {
        setTimeout(() => {
            term1.clear();
            emitWithCsrf('attach_session', { terminal: 1, session: currentSession1 });
            setTimeout(doFit, 100);
        }, 200);
    }
    if (currentSession2 && paneTypes[2] === 'terminal' && isSplit) {
        setTimeout(() => {
            term2.clear();
            emitWithCsrf('attach_session', { terminal: 2, session: currentSession2 });
            setTimeout(doFit, 100);
        }, 200);
    }
}

// Called from core.js on socket 'connected'
function onSocketConnected() {
    if (window._urlParamsProcessed) return;
    const params = new URLSearchParams(window.location.search);
    const urlSession = params.get('session');
    const urlSession2 = params.get('session2');
    const urlSplit = params.get('split') === '1';
    if (!currentSession1 && paneTypes[1] !== 'browser') {
        if (urlSession && urlSession.startsWith('chat:')) {
            openChatPane(urlSession.slice(5));
        } else if (urlSession === 'desktop') {
            toggleDesktop();
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
        terminal.classList.remove('hidden');
        browser.classList.add('hidden');
        setTimeout(doFit, 100);
    } else {
        paneTypes[activePane] = 'browser';
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
function updateSessionList(sessions, chatSessions) {
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
    const newKey = JSON.stringify([...sessions].sort()) + '|' + JSON.stringify(chatKeys.sort());
    if (sessionListInitialized && lastSessionsKey === newKey) return;
    sessionListInitialized = true;
    lastSessionsKey = newKey;

    sessionList.innerHTML = '';

    // Desktop item
    const desktopItem = document.createElement('div');
    desktopItem.className = 'session-item';
    desktopItem.dataset.session = 'desktop';
    const desktopName = document.createElement('span');
    desktopName.className = 'session-name';
    desktopName.textContent = 'Desktop';
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

    // Terminal sessions
    sessions.forEach(session => {
        const item = document.createElement('div');
        item.className = 'session-item';
        item.dataset.session = session;
        const nameSpan = document.createElement('span');
        nameSpan.className = 'session-name';
        nameSpan.textContent = session;
        const closeBtn = document.createElement('button');
        closeBtn.className = 'close-btn';
        closeBtn.textContent = '✕';
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
        nameSpan.textContent = chat.name;
        const closeBtn = document.createElement('button');
        closeBtn.className = 'close-btn';
        closeBtn.textContent = '✕';
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
    if (showArchived) emitWithCsrf('acp_list_archived');
}

socket.on('sessions_list', data => { updateSessionList(data.sessions, data.chat_sessions || []); });
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
socket.on('session_closed', () => { emitWithCsrf('get_sessions'); });

// --- Attach / Detach ---
function attachSession(sessionName) {
    if (sessionName === 'desktop') { toggleDesktop(); return; }
    if (paneTypes[activeTerminal] === 'browser') {
        const browser = document.getElementById(`browser${activeTerminal}`);
        const terminal = document.getElementById(`terminal${activeTerminal}`);
        paneTypes[activeTerminal] = 'terminal';
        terminal.classList.remove('hidden');
        browser.classList.add('hidden');
    }
    updateKbdBtn();
    const term = activeTerminal === 1 ? term1 : term2;
    term.clear();
    if (activeTerminal === 1) { currentSession1 = sessionName; sessionStorage.setItem('fernando_session1', sessionName); }
    else { currentSession2 = sessionName; sessionStorage.setItem('fernando_session2', sessionName); }
    emitWithCsrf('attach_session', { terminal: activeTerminal, session: sessionName });
    highlightSidebarItem(sessionName);
    term.focus();
    setTimeout(doFit, 100);
    syncUrlParams();
}

function getBrowserPaneSession(pane) {
    const iframe = document.querySelector(`#browser${pane} iframe`);
    if (iframe && iframe.src) {
        const m = iframe.src.match(/\/chat\/([^/?#]+)/);
        if (m) return 'chat:' + m[1];
    }
    return 'desktop';
}

function syncUrlParams() {
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
        setActiveTerminal(2);
    } else {
        const keep = activeTerminal;
        const discard = keep === 1 ? 2 : 1;
        document.getElementById(`terminal${discard}-container`).classList.add('hidden');
        setActiveTerminal(keep);
    }
    setTimeout(doFit, 100);
    syncUrlParams();
}

function setActiveTerminal(termNum) {
    activeTerminal = termNum;
    const c1 = document.getElementById('terminal1-container');
    const c2 = document.getElementById('terminal2-container');
    c1.classList.toggle('active', termNum === 1);
    c2.classList.toggle('active', termNum === 2);
    if (isSplit) { c1.classList.add('split-mode'); c2.classList.add('split-mode'); }
    else { c1.classList.remove('split-mode'); c2.classList.remove('split-mode'); }
    updateMobileControls();
    syncUrlParams();
}

// Click handlers
function syncPaneSidebar(paneNum) {
    const s = paneTypes[paneNum] === 'browser' ? getBrowserPaneSession(paneNum) : (paneNum === 1 ? currentSession1 : currentSession2);
    if (s) highlightSidebarItem(s);
}
document.getElementById('terminal1-container').addEventListener('mousedown', (e) => {
    setActiveTerminal(1);
    syncPaneSidebar(1);
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
});
document.getElementById('terminal2-container').addEventListener('mousedown', (e) => {
    if (isSplit) {
        setActiveTerminal(2);
        syncPaneSidebar(2);
    }
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
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
