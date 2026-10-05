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
    if (paneController.get(1).terminalSession && paneController.get(1).surface === 'terminal') {
        setTimeout(() => {
            _paneSession[1] = paneController.get(1).terminalSession;
            showTermInPane(paneController.get(1).terminalSession, 1);
            emitWithCsrf('attach_session', { terminal: 1, session: paneController.get(1).terminalSession, skip_replay: true });
            setTimeout(doFit, 100);
        }, 200);
    }
    if (paneController.get(2).terminalSession && paneController.get(2).surface === 'terminal' && isSplit) {
        setTimeout(() => {
            _paneSession[2] = paneController.get(2).terminalSession;
            showTermInPane(paneController.get(2).terminalSession, 2);
            emitWithCsrf('attach_session', { terminal: 2, session: paneController.get(2).terminalSession, skip_replay: true });
            setTimeout(doFit, 100);
        }, 200);
    }
    setTimeout(() => {
        const activeTerm = activeTerminal === 1 ? term1 : term2;
        if (paneController.get(activeTerminal).surface === 'terminal' && activeTerm) activeTerm.focus();
        // iOS: re-toggle spacers after returning from background
        if (/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
            document.querySelectorAll('.ios-spacer').forEach(s => {
                s.style.display = 'none';
                setTimeout(() => { s.style.display = ''; }, 100);
            });
        }
    }, 400);
}

function syncActiveChatPanes() {
    // Collect chat session IDs from both panes and tell backend which are active
    const chatIds = [];
    for (const pane of [1, 2]) {
        if (paneController.get(pane).surface !== 'browser') continue;
        const iframe = document.querySelector(`#browser${pane} iframe`);
        if (!iframe || !iframe.src) continue;
        const m = iframe.src.match(/\/chat\/([^/?#]+)/);
        if (m) chatIds.push(m[1]);
    }
    emitWithCsrf('acp_set_active_panes', { session_ids: chatIds });
}

// Called from core.js on socket 'connected'
function onSocketConnected() {
    // Re-register active panes on every connect/reconnect to prevent idle reaper killing them
    syncActiveChatPanes();
    applyProviderSettings();
    if (window._urlParamsProcessed) { handleForeground(); return; }
    if (!paneController.restore() && paneController.get(1).terminalSession) {
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
        if (paneController.get(pn).surface !== 'browser') continue;
        const iframe = document.getElementById('browser' + pn).querySelector('iframe');
        if (!iframe || !iframe.src.includes('/kasm/')) continue;
        try {
            const btn = iframe.contentDocument.getElementById('noVNC_keyboard_button');
            if (btn) { btn.click(); return; }
        } catch(e) {}
    }
}

function updateKbdBtn() {
    const hasDesktop = [...paneController.sessionTypes.values()].some(adapter => adapter.keyboardButton && [1, 2].some(pn => paneController.isEmbedded(pn, adapter.type)));
    document.getElementById('kbdBtn').classList.toggle('kbdVisible', hasDesktop);
    const hasTerminal = paneController.get(activeTerminal).surface === 'terminal';
    document.getElementById('resizeBtn').classList.toggle('resizeBtnVisible', hasTerminal);
    updateMobileControls();
}

function toggleDesktop() {
    return paneController.open('desktop');
}

// --- Jupyter ---
let _jupyterCounter = 0;
let _jupyterNamePaths = {}; // Track path by session name for sidebar restoration

// Get the group of the session in the active pane (used for default placement)
// Returns null if the active pane has no session or an ungrouped session
function getActivePaneGroupId() {
    const paneSession = paneController.get(activeTerminal).contentKey;
    const termSession = paneController.get(activeTerminal).terminalSession;
    const sessionKey = paneSession || termSession;
    if (sessionKey) {
        const groupId = _cachedSessionGroups[sessionKey];
        if (groupId !== undefined && groupId !== null) {
            return groupId;
        }
    }
    return null;
}

function openJupyter(name) {
    return paneController.open(name ? 'jupyter:' + name : 'jupyter');
}

function mountJupyter({ name, pane: activePane, browser }) {
    browser.innerHTML = '';
    browser.style.background = '#0d2848';
    const iframe = document.createElement('iframe');
    // Restore from URL-encoded path, or saved name→path map, otherwise construct from name
    let jpath = null;
    paneController.get(activePane).jupyterPath = null;
    if (name.startsWith('/jupyter/')) {
        jpath = name;
        // Extract display name from path
        if (jpath.includes('/notebooks/')) name = decodeURIComponent(jpath.split('/notebooks/')[1] || '').replace('.ipynb', '');
        else if (jpath.includes('/tree/')) name = decodeURIComponent(jpath.split('/tree/')[1] || '').replace(/\/+$/, '') || 'Jupyter';
        else if (jpath.includes('/edit/')) name = decodeURIComponent(jpath.split('/edit/')[1] || '').replace(/\/+$/, '');
        else name = 'Jupyter';
        paneController.get(activePane).contentKey = 'jupyter:' + name;
        _jupyterNamePaths[name] = jpath;
    } else if (_jupyterNamePaths[name]) {
        jpath = _jupyterNamePaths[name];
    }
    if (jpath) {
        iframe.src = jpath + (jpath.includes('?') ? '&' : '?') + 'api_key=' + encodeURIComponent(window.FERNANDO_API_KEY);
    } else {
        const isTreeView = name === 'Jupyter' || name === 'Jupyter Notebook' || name === 'Home' || name.match(/^Jupyter-\d+$/);
        const path = isTreeView ? '' : 'notebooks/' + encodeURIComponent(name) + '.ipynb';
        iframe.src = `/jupyter/${path}?api_key=` + encodeURIComponent(window.FERNANDO_API_KEY);
    }
    iframe.style.cssText = 'width:100%;height:100%;border:none;background:#0d2848';
    browser.appendChild(iframe);
    return { name };
}

// --- Notes (Notebooks) ---
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
    return paneController.open('notebook:' + notebook);
}

function showNotebookPicker() {
    // Preserve target group from new session modal before closing it
    window._notebookTargetGroupId = newSessionTargetGroupId;
    closeNewSessionModal();
    document.getElementById('notebookPickerModal').classList.add('open');
    emitWithCsrf('list_notebooks');
}

function closeNotebookPicker() {
    document.getElementById('notebookPickerModal').classList.remove('open');
    window._notebookTargetGroupId = null;
}

function openSelectedNotebook() {
    const sel = document.getElementById('notebookSelect');
    const name = sel.value;
    if (!name) return;
    const savedGroupId = window._notebookTargetGroupId;
    closeNotebookPicker();
    window._notebookTargetGroupId = savedGroupId;
    openNotebook(name);
}

function promptCreateNotebook() {
    // Preserve target group through to create modal
    const savedGroupId = window._notebookTargetGroupId;
    document.getElementById('notebookPickerModal').classList.remove('open');
    window._notebookTargetGroupId = savedGroupId;
    const modal = document.getElementById('notebookCreateModal');
    const input = document.getElementById('notebookNameInput');
    input.value = '';
    modal.classList.add('open');
    input.focus();
}

function closeCreateNotebook() {
    document.getElementById('notebookCreateModal').classList.remove('open');
    window._notebookTargetGroupId = null;
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
        if (paneController.get(pn).contentKey === data.name) {
            paneController.get(pn).contentKey = null;
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

// Forward Jupyter commands from websocket to the Jupyter iframe
socket.on('jupyter_cmd', (data) => {
    const receivers = [];
    const target = (data.notebook || '').replace(/\.ipynb$/i, '');
    for (const pn of [1, 2]) {
        const iframe = document.querySelector(`#browser${pn} iframe`);
        if (iframe && iframe.src && iframe.src.includes('/jupyter/') && iframe.contentWindow) {
            // Extract notebook name from iframe URL (e.g. /jupyter/notebooks/Untitled4.ipynb or /jupyter/notebooks/sub/dir/ee44.ipynb)
            const m = iframe.src.match(/\/notebooks\/(.+?)\.ipynb/);
            const iframeName = m ? decodeURIComponent(m[1]).split('/').pop() : '';
            if (target && iframeName !== target) continue;
            iframe.contentWindow.postMessage({type: 'jupyter-cmd', ...data}, window.location.origin);
            receivers.push(pn);
        }
    }
    // Emit an ack so MCP knows how many iframes received the command
    emitWithCsrf('jupyter_cmd_ack', {
        id: data.id || '',
        receivers: receivers.length,
    });
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
    paneController.ready('notebook:' + data.name);
});

socket.on('notebook_error', (data) => {
    showAlert('Notebook error: ' + data.error);
    // Revert pane if it was waiting
    for (const pn of [1, 2]) {
        if (paneController.get(pn).surface === 'browser' && paneController.get(pn).contentKey) {
            const browser = document.getElementById(`browser${pn}`);
            if (browser.querySelector('iframe') === null) {
                paneController.showTerminal(pn);
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
        if (paneController.get(n).surface === 'browser') {
            const b = document.getElementById(`browser${n}`);
            if (b) { b.innerHTML = ''; ensureDesktopIframe(b); }
        }
    });
});

function setPaneType(paneNum, type) {
    paneController.get(paneNum).surface = type;
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
function highlightSidebarItem(sessionKey, isSecondary, fast = false) {
    const item = document.querySelector(`.session-item[data-session="${sessionKey}"]`);
    
    if (isSecondary) {
        document.querySelectorAll('.session-item').forEach(el => el.classList.remove('secondary'));
        if (item) item.classList.add('secondary');
    } else {
        const currentActive = document.querySelector('.session-item.active');
        const isFocusSwitch = fast || (item && item.classList.contains('secondary'));
        const duration = isFocusSwitch ? 50 : 100;
        
        if (currentActive && currentActive !== item) {
            if (isFocusSwitch) currentActive.classList.add('fast');
            currentActive.classList.add('deselecting');
            currentActive.classList.remove('active');
            setTimeout(() => currentActive.classList.remove('deselecting', 'fast'), duration);
        } else if (currentActive === item) {
            return;
        }
        document.querySelectorAll('.session-item:not(.deselecting)').forEach(el => el.classList.remove('active'));
        if (item) {
            item.classList.remove('secondary', 'deselecting');
            if (isFocusSwitch) item.classList.add('fast');
            item.classList.add('active');
            if (isFocusSwitch) setTimeout(() => item.classList.remove('fast'), duration);
        }
    }
}

// Refresh both active and secondary highlights for split mode
function refreshSidebarHighlights() {
    const s1 = paneController.sessionKey(1);
    const s2 = paneController.sessionKey(2);
    
    if (isSplit) {
        if (activeTerminal === 1) {
            if (s1) highlightSidebarItem(s1, false);
            if (s2) highlightSidebarItem(s2, true);
        } else {
            if (s2) highlightSidebarItem(s2, false);
            if (s1) highlightSidebarItem(s1, true);
        }
    } else {
        const s = activeTerminal === 1 ? s1 : s2;
        if (s) highlightSidebarItem(s, false);
    }
}

function updatePaneBorders() {
    const c1 = document.getElementById('terminal1-container');
    const c2 = document.getElementById('terminal2-container');
    
    // Reset borders
    c1.style.removeProperty('border-color');
    c2.style.removeProperty('border-color');
    
    if (!isSplit) return;
    
    // Get session keys for both panes
    const s1 = paneController.sessionKey(1);
    const s2 = paneController.sessionKey(2);
    
    const defaultColor = '#3465a3';
    
    // Apply group colors to pane borders (or default blue for ungrouped)
    function applyColor(container, sessionKey, isActive) {
        if (!sessionKey) return;
        const groupId = _cachedSessionGroups[sessionKey];
        let color = defaultColor;
        if (groupId) {
            const group = _cachedGroups.find(g => g.id === groupId);
            if (group && group.color) {
                color = group.color;
            }
        }
        container.style.borderColor = isActive ? color : color + '60';
    }
    
    const activePane = activeTerminal;
    applyColor(c1, s1, activePane === 1);
    applyColor(c2, s2, activePane === 2);
}

let sessionListInitialized = false;
let lastSessionsKey = '';
let _cachedChatSessions = [];
let _cachedSessions = [];
let _cachedData = {};

socket.on('acp_status_change', (data) => {
    const session = _cachedChatSessions.find(c => c.id === data.session_id);
    if (session) {
        session.status = data.status;
        updateSessionList(_cachedSessions, _cachedChatSessions, _cachedData);
    }
});

// --- Project Groups ---
// Per-browser expand state: localStorage key -> expanded group IDs set
// Per-browser group order: localStorage key -> ordered group IDs array
const GROUP_ORDER_KEY = 'fernando_group_order';

function getCollapsedGroups() {
    const params = new URLSearchParams(window.location.search);
    const collapsed = params.get('collapsed');
    if (!collapsed) return new Set();
    return new Set(collapsed.split(',').filter(id => id));
}
function setCollapsedGroups(collapsed) {
    const params = new URLSearchParams(window.location.search);
    if (collapsed.size === 0) {
        params.delete('collapsed');
    } else {
        params.set('collapsed', [...collapsed].join(','));
    }
    const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState(null, '', newUrl);
}
function isGroupExpanded(groupId) {
    return !getCollapsedGroups().has(groupId);
}
function toggleGroupExpanded(groupId) {
    const collapsed = getCollapsedGroups();
    if (collapsed.has(groupId)) {
        collapsed.delete(groupId);
    } else {
        collapsed.add(groupId);
    }
    setCollapsedGroups(collapsed);
    return !collapsed.has(groupId);
}

// Parent agent collapse state (localStorage)
const COLLAPSED_PARENTS_KEY = 'fernando_collapsed_parents';
function getCollapsedParents() {
    try {
        const raw = localStorage.getItem(COLLAPSED_PARENTS_KEY) || '[]';
        return new Set(JSON.parse(raw));
    } catch { return new Set(); }
}
function setCollapsedParents(collapsed) {
    localStorage.setItem(COLLAPSED_PARENTS_KEY, JSON.stringify([...collapsed]));
}
function isParentExpanded(parentId) {
    return !getCollapsedParents().has(parentId);
}
function toggleParentExpanded(parentId) {
    const collapsed = getCollapsedParents();
    if (collapsed.has(parentId)) {
        collapsed.delete(parentId);
    } else {
        collapsed.add(parentId);
    }
    setCollapsedParents(collapsed);
    if (_cachedData && _cachedSessions !== undefined && _cachedChatSessions !== undefined) {
        updateSessionList(_cachedSessions, _cachedChatSessions, _cachedData);
    } else {
        emitWithCsrf('get_sessions');
    }
    return !collapsed.has(parentId);
}

function getGroupOrder() {
    try {
        return JSON.parse(localStorage.getItem(GROUP_ORDER_KEY) || '[]');
    } catch { return []; }
}
function setGroupOrder(order) {
    localStorage.setItem(GROUP_ORDER_KEY, JSON.stringify(order));
}

// Cached group data from server
let _cachedGroups = [];
let _cachedSessionGroups = {};

// Pastel color palette for groups
const GROUP_COLORS = [
    '#263fce', // blue (default) - vibrant blue
    '#3d8b40', // green - darker for white text
    '#b8860b', // yellow/gold - dark goldenrod
    '#c45c26', // orange - darker
    '#a62c2c', // red - crimson
    '#8b4dab', // purple - darker
    '#2a8a8a', // cyan/teal - darker
    '#a85d8a', // rose - darker
];

function chatSidebarLabel(chat) {
    const suffix = chat.name.match(/ \(fork(?:@(\d+))?\)$/);
    const kind = chat.session_kind || (suffix ? 'fork' : chat.parent_id ? 'subagent' : null);
    if (kind === 'fork') {
        const turn = chat.fork_turn ?? (suffix && suffix[1]) ?? '?';
        return { prefix: `FORK@${turn}:`, name: chat.name.replace(/(?: \(fork(?:@\d+)?\))+$/, '') };
    }
    return { prefix: kind === 'subagent' ? 'SUBAGENT:' : '', name: chat.name };
}

// Draw only relationships that are contiguous and visible in this group.
// Rows stay flat so selection, drag/drop, and collapse keep their existing behavior.
function connectSessionTree(sessionItems) {
    const stack = [];
    const nodes = [];
    sessionItems.forEach(item => {
        const parentIndex = stack.findIndex(node => node.id === item.dataset.treeParent);
        stack.length = parentIndex < 0 ? 0 : parentIndex + 1;
        const parent = stack[stack.length - 1] || null;
        const node = { item, id: item.dataset.treeId, parent, children: [], depth: stack.length };
        node.root = parent ? parent.root : node;
        if (parent) parent.children.push(node);
        nodes.push(node);
        if (node.id) stack.push(node);
    });
    nodes.forEach((node, index) => {
        // Only fade separators inside one agent's contiguous descendant tree.
        const next = nodes[index + 1];
        node.item.classList.toggle('tree-joined', !!(node.id && next && next.root === node.root));
        if (!node.id) return;
        node.item.style.setProperty('--tree-depth', node.depth);
        const gutter = document.createElement('span');
        gutter.className = 'session-tree';
        gutter.setAttribute('aria-hidden', 'true');
        function segment(level, kind) {
            const line = document.createElement('span');
            line.className = 'session-tree-line ' + kind;
            line.style.setProperty('--tree-level', level);
            gutter.appendChild(line);
        }
        if (node.parent) {
            const siblings = node.parent.children;
            segment(node.depth - 1, siblings[siblings.length - 1] === node ? 'branch last' : 'branch');
        }
        for (let ancestor = node.parent; ancestor && ancestor.parent; ancestor = ancestor.parent) {
            const siblings = ancestor.parent.children;
            if (siblings[siblings.length - 1] !== ancestor) segment(ancestor.depth - 1, 'continuation');
        }
        if (node.children.length) segment(node.depth, 'stem');
        node.item.appendChild(gutter);
    });
}

function createGroupElement(group, sessionItems, isExpanded, isFirst) {
    const wrapper = document.createElement('div');
    wrapper.className = 'group-wrapper';
    wrapper.dataset.groupId = group.id;
    
    const isUngrouped = group.id === '__ungrouped__';
    
    // Apply group color as subtle background tint (not for Ungrouped)
    const color = group.color || '#7ea8e3';
    if (!isUngrouped) {
        wrapper.style.setProperty('--group-color', color);
        wrapper.style.background = color + '33'; // ~20% opacity tint
    }
    
    const header = document.createElement('div');
    header.className = 'group-header';
    header.draggable = !isUngrouped; // Can't drag the Ungrouped group
    header.dataset.groupId = group.id;
    
    // First group gets top border for transition from Desktop
    if (isFirst) {
        const borderColor = color ? `color-mix(in srgb, ${color} 25%, transparent)` : '#143151';
        header.style.borderTop = `1px solid ${borderColor}`;
    }
    
    const chevron = document.createElement('span');
    chevron.className = 'group-chevron' + (isExpanded ? ' expanded' : '');
    chevron.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="3 2 7 5 3 8"/></svg>';
    
    // Only show color dot for real groups
    const colorDot = document.createElement('span');
    colorDot.className = 'group-color-dot';
    if (!isUngrouped) {
        colorDot.style.background = color;
    } else {
        colorDot.style.display = 'none';
    }
    
    const nameSpan = document.createElement('span');
    nameSpan.className = 'group-name';
    nameSpan.textContent = group.name;
    
    header.appendChild(chevron);
    header.appendChild(colorDot);
    header.appendChild(nameSpan);
    
    const body = document.createElement('div');
    body.className = 'group-body' + (isExpanded ? ' expanded' : '');
    connectSessionTree(sessionItems);
    sessionItems.forEach(item => {
        // Apply group color to session items (not for Ungrouped)
        if (!isUngrouped) {
            item.style.setProperty('--group-color', color);
        }
        body.appendChild(item);
    });
    
    // Click to expand/collapse
    header.addEventListener('click', (e) => {
        if (e.target.closest('.group-color-dot')) return; // handled separately
        const nowExpanded = toggleGroupExpanded(group.id);
        chevron.classList.toggle('expanded', nowExpanded);
        body.classList.toggle('expanded', nowExpanded);
    });
    
    // Double-click to rename (not for Ungrouped)
    let clickTimer = null;
    nameSpan.addEventListener('click', (e) => {
        e.stopPropagation();
        if (e.detail === 2 && !isUngrouped) {
            clearTimeout(clickTimer);
            startGroupRename(group.id, nameSpan);
        } else {
            clickTimer = setTimeout(() => {
                const nowExpanded = toggleGroupExpanded(group.id);
                chevron.classList.toggle('expanded', nowExpanded);
                body.classList.toggle('expanded', nowExpanded);
            }, 250);
        }
    });
    
    // Color picker on dot click (not for Ungrouped)
    if (!isUngrouped) {
        colorDot.addEventListener('click', (e) => {
            e.stopPropagation();
            showGroupColorPicker(group.id, colorDot);
        });
        colorDot.addEventListener('touchend', (e) => {
            e.stopPropagation();
            e.preventDefault();
            showGroupColorPicker(group.id, colorDot);
        });
    }
    
    // Context menu (right-click) for rename/delete
    header.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        showGroupContextMenu(group.id, e.clientX, e.clientY, sessionItems.length);
    });
    
    // Long-press for mobile
    let holdTimer = null;
    header.addEventListener('touchstart', (e) => {
        holdTimer = setTimeout(() => {
            holdTimer = 'fired';
            showGroupContextMenu(group.id, e.touches[0].clientX, e.touches[0].clientY, sessionItems.length);
        }, 500);
    }, {passive: true});
    header.addEventListener('touchend', () => { if (holdTimer !== 'fired') clearTimeout(holdTimer); holdTimer = null; });
    header.addEventListener('touchmove', () => { 
        if (holdTimer === 'fired') dismissContextMenus();
        else clearTimeout(holdTimer); 
    });
    
    // Drag-and-drop for group reordering
    header.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('application/x-group-id', group.id);
        e.dataTransfer.effectAllowed = 'move';
        header.classList.add('dragging');
    });
    header.addEventListener('dragend', () => header.classList.remove('dragging'));
    
    wrapper.appendChild(header);
    wrapper.appendChild(body);
    
    // Drop zone for sessions - on the whole wrapper so it works even when collapsed
    setupDropZone(wrapper, group.id);
    setupGroupDropZone(wrapper, group.id);
    
    return wrapper;
}

function startGroupRename(groupId, nameSpan) {
    const oldName = nameSpan.textContent;
    const input = document.createElement('input');
    input.value = oldName;
    input.className = 'group-rename-input';
    nameSpan.replaceWith(input);
    input.focus();
    input.select();
    function commit() {
        if (!input.parentNode) return;
        const newName = input.value.trim();
        nameSpan.textContent = newName || oldName;
        input.replaceWith(nameSpan);
        if (newName && newName !== oldName) {
            emitWithCsrf('group_rename', { group_id: groupId, name: newName });
        }
    }
    input.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); commit(); }
        if (e.key === 'Escape') {
            nameSpan.textContent = oldName;
            input.replaceWith(nameSpan);
        }
    });
    input.addEventListener('blur', commit);
    input.addEventListener('click', (e) => e.stopPropagation());
}

function showGroupColorPicker(groupId, anchor) {
    // Remove any existing picker
    document.querySelectorAll('.group-color-picker').forEach(p => p.remove());
    
    const picker = document.createElement('div');
    picker.className = 'group-color-picker';
    
    GROUP_COLORS.forEach(color => {
        const swatch = document.createElement('div');
        swatch.className = 'color-swatch';
        swatch.style.background = color;
        swatch.addEventListener('click', () => {
            emitWithCsrf('group_set_color', { group_id: groupId, color: color });
            picker.remove();
        });
        picker.appendChild(swatch);
    });
    
    document.body.appendChild(picker);
    const rect = anchor.getBoundingClientRect();
    picker.style.left = rect.left + 'px';
    picker.style.top = (rect.bottom + 4) + 'px';
    
    // Close on outside click/touch
    setTimeout(() => {
        function closePicker(e) {
            if (!picker.contains(e.target)) {
                picker.remove();
                document.removeEventListener('click', closePicker);
                document.removeEventListener('touchstart', closePicker);
            }
        }
        document.addEventListener('click', closePicker);
        document.addEventListener('touchstart', closePicker);
    }, 10);
}

let contextMenuCleanup = null;

function dismissContextMenus() {
    if (contextMenuCleanup) {
        contextMenuCleanup();
        contextMenuCleanup = null;
    }
    closeActiveSubmenu();
    document.querySelectorAll('.group-context-menu').forEach(m => m.remove());
}

function trackContextMenu(menu, templatesBtn = null, groupId = null) {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    let dismissTimer = null;
    let openTimer = null;
    let submenuTimer = null;
    let templatesRequested = false;
    const cancelOpen = () => {
        clearTimeout(openTimer);
        openTimer = null;
    };
    const scheduleDismiss = () => {
        cancelOpen();
        if (dismissTimer === null) dismissTimer = setTimeout(dismissContextMenus, 250);
    };
    const openTemplates = () => {
        cancelOpen();
        templatesRequested = true;
        clearTimeout(submenuTimer);
        submenuTimer = null;
        const rect = templatesBtn.getBoundingClientRect();
        showGroupTemplatesSubmenu(groupId, menu, rect.right, rect.top);
    };
    document.addEventListener('pointermove', e => {
        if (e.pointerType === 'touch') return;
        const inSubmenu = activeSubmenu && activeSubmenu.contains(e.target);
        if (menu.contains(e.target) || inSubmenu) {
            clearTimeout(dismissTimer);
            dismissTimer = null;
        } else {
            scheduleDismiss();
        }
        if (templatesBtn && (templatesBtn.contains(e.target) || inSubmenu)) {
            clearTimeout(submenuTimer);
            submenuTimer = null;
            if (!templatesRequested && openTimer === null && templatesBtn.contains(e.target)) {
                openTimer = setTimeout(openTemplates, 200);
            }
        } else {
            cancelOpen();
            if (submenuTimer === null) submenuTimer = setTimeout(() => {
                closeActiveSubmenu();
                templatesRequested = false;
                submenuTimer = null;
            }, 250);
        }
    }, options);
    document.addEventListener('pointerout', e => {
        if (e.pointerType !== 'touch' && (!e.relatedTarget || e.relatedTarget.tagName === 'IFRAME')) scheduleDismiss();
    }, options);
    document.addEventListener('pointerdown', e => {
        if (!menu.contains(e.target) && !(activeSubmenu && activeSubmenu.contains(e.target))) dismissContextMenus();
    }, options);
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') dismissContextMenus();
    }, options);
    window.addEventListener('blur', dismissContextMenus, options);
    window.addEventListener('resize', dismissContextMenus, options);
    menu.addEventListener('click', e => {
        if (templatesBtn && templatesBtn.contains(e.target)) openTemplates();
        else dismissContextMenus();
    }, options);
    contextMenuCleanup = () => {
        controller.abort();
        clearTimeout(dismissTimer);
        clearTimeout(submenuTimer);
        cancelOpen();
    };
}

function showSessionContextMenu(sessionKey, x, y, onRename, onClose, onSleep, onClone, onCollapse) {
    dismissContextMenus();
    
    const menu = document.createElement('div');
    menu.className = 'group-context-menu';
    
    if (sessionKey.startsWith('chat:')) {
        const chatId = sessionKey.slice(5);
        const idRow = document.createElement('div');
        idRow.className = 'context-menu-item context-menu-id-row';
        idRow.style.cssText = 'display:flex;align-items:center;gap:8px;font-family:monospace;font-size:12px;cursor:default;';
        
        const idText = document.createElement('span');
        idText.textContent = chatId;
        idText.style.color = '#7aa2f7';
        
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'context-menu-copy-btn';
        copyBtn.style.cssText = 'background:none;border:none;padding:2px;cursor:pointer;display:flex;align-items:center;color:#666;';
        copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>';
        copyBtn.title = 'Copy chat ID';
        copyBtn.onclick = async (e) => {
            e.stopPropagation();
            await navigator.clipboard.writeText(chatId);
            copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
            setTimeout(() => {
                copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>';
            }, 1500);
        };
        
        idRow.appendChild(idText);
        idRow.appendChild(copyBtn);
        menu.appendChild(idRow);
    }

    if (onRename) {
        const renameBtn = document.createElement('div');
        renameBtn.className = 'context-menu-item';
        renameBtn.textContent = 'Rename';
        renameBtn.onclick = () => { menu.remove(); onRename(); };
        menu.appendChild(renameBtn);
    }
    
    if (onClone) {
        const cloneBtn = document.createElement('div');
        cloneBtn.className = 'context-menu-item';
        cloneBtn.textContent = 'Fork';
        cloneBtn.onclick = () => { menu.remove(); onClone(); };
        menu.appendChild(cloneBtn);
    }
    
    if (onCollapse) {
        const collapseBtn = document.createElement('div');
        collapseBtn.className = 'context-menu-item';
        collapseBtn.textContent = onCollapse.isCollapsed ? 'Expand Subagents' : 'Collapse Subagents';
        collapseBtn.onclick = () => { menu.remove(); onCollapse.toggle(); };
        menu.appendChild(collapseBtn);
    }
    
    if (onSleep) {
        const chat = _cachedChatSessions.find(c => 'chat:' + c.id === sessionKey);
        const sleeping = chat && !chat.loaded;
        const sleepBtn = document.createElement('div');
        sleepBtn.className = 'context-menu-item';
        sleepBtn.textContent = sleeping ? 'Wake' : 'Sleep';
        sleepBtn.onclick = () => {
            menu.remove();
            if (sleeping) emitWithCsrf('acp_wake', { session_id: chat.id });
            else onSleep();
        };
        menu.appendChild(sleepBtn);
    }
    
    if (onClose) {
        const closeBtn = document.createElement('div');
        closeBtn.className = 'context-menu-item danger';
        closeBtn.textContent = paneController.adapter(sessionKey)?.closeLabel || 'Close';
        closeBtn.onclick = () => { menu.remove(); onClose(); };
        menu.appendChild(closeBtn);
    }
    
    document.body.appendChild(menu);
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    
    const menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) menu.style.left = (window.innerWidth - menuRect.width - 10) + 'px';
    if (menuRect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - menuRect.height - 10) + 'px';
    
    trackContextMenu(menu);
}

function showGroupContextMenu(groupId, x, y, sessionCount) {
    dismissContextMenus();

    const menu = document.createElement('div');
    menu.className = 'group-context-menu';

    const newSessionBtn = document.createElement('div');
    newSessionBtn.className = 'context-menu-item';
    newSessionBtn.textContent = 'New Session';
    newSessionBtn.onclick = () => {
        menu.remove();
        openNewSessionModal(groupId);
    };
    menu.appendChild(newSessionBtn);

    if (groupId === '__ungrouped__') {
        document.body.appendChild(menu);
        const menuRect = menu.getBoundingClientRect();
        menu.style.left = Math.max(0, Math.min(x, window.innerWidth - menuRect.width - 10)) + 'px';
        menu.style.top = Math.max(0, Math.min(y, window.innerHeight - menuRect.height - 10)) + 'px';
        trackContextMenu(menu);
        return;
    }

    const templatesBtn = document.createElement('div');
    templatesBtn.className = 'context-menu-item has-submenu';
    templatesBtn.textContent = 'Context templates';
    const arrow = document.createElement('span');
    arrow.className = 'submenu-arrow';
    arrow.textContent = '▸';
    templatesBtn.appendChild(arrow);
    menu.appendChild(templatesBtn);
    
    const renameBtn = document.createElement('div');
    renameBtn.className = 'context-menu-item';
    renameBtn.textContent = 'Rename';
    renameBtn.onclick = () => {
        menu.remove();
        const nameSpan = document.querySelector(`.group-wrapper[data-group-id="${groupId}"] .group-name`);
        if (nameSpan) startGroupRename(groupId, nameSpan);
    };
    menu.appendChild(renameBtn);
    
    const wakeAllBtn = document.createElement('div');
    wakeAllBtn.className = 'context-menu-item';
    wakeAllBtn.textContent = 'Wake All Chats';
    wakeAllBtn.onclick = () => {
        menu.remove();
        emitWithCsrf('acp_wake_group', { group_id: groupId });
    };
    menu.appendChild(wakeAllBtn);
    
    const sleepAllBtn = document.createElement('div');
    sleepAllBtn.className = 'context-menu-item';
    sleepAllBtn.textContent = 'Sleep All Chats';
    sleepAllBtn.onclick = () => {
        menu.remove();
        emitWithCsrf('acp_sleep_group', { group_id: groupId });
    };
    menu.appendChild(sleepAllBtn);
    
    const archiveBtn = document.createElement('div');
    archiveBtn.className = 'context-menu-item danger';
    archiveBtn.textContent = 'Archive';
    archiveBtn.onclick = () => {
        menu.remove();
        emitWithCsrf('group_archive', { group_id: groupId });
    };
    menu.appendChild(archiveBtn);

    const deleteBtn = document.createElement('div');
    deleteBtn.className = 'context-menu-item danger';
    deleteBtn.textContent = sessionCount > 0 ? 'Delete (move sessions out)' : 'Delete';
    deleteBtn.onclick = () => {
        menu.remove();
        emitWithCsrf('group_delete', { group_id: groupId });
    };
    menu.appendChild(deleteBtn);
    
    document.body.appendChild(menu);
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    
    // Keep menu in viewport
    const menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) menu.style.left = (window.innerWidth - menuRect.width - 10) + 'px';
    if (menuRect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - menuRect.height - 10) + 'px';
    
    trackContextMenu(menu, templatesBtn, groupId);
}

function setupDropZone(element, groupId) {
    let dragCounter = 0; // Track enter/leave for child elements
    
    // Get the group color for this drop zone
    function getGroupColor() {
        if (groupId === '__ungrouped__' || !groupId) return '#3465a3'; // default blue
        const group = _cachedGroups.find(g => g.id === groupId);
        return group && group.color ? group.color : '#3465a3';
    }
    
    element.addEventListener('dragenter', (e) => {
        if (e.dataTransfer.types.includes('application/x-session-key')) {
            e.preventDefault();
            dragCounter++;
            element.classList.add('drop-target');
            // Apply group-colored styling
            const color = getGroupColor();
            element.style.outline = `2px dashed ${color}`;
            element.style.background = `${color}20`;
        }
    });
    element.addEventListener('dragover', (e) => {
        if (e.dataTransfer.types.includes('application/x-session-key')) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        }
    });
    element.addEventListener('dragleave', (e) => {
        dragCounter--;
        if (dragCounter === 0) {
            element.classList.remove('drop-target');
            element.style.removeProperty('outline');
            element.style.removeProperty('background');
        }
    });
    element.addEventListener('drop', (e) => {
        dragCounter = 0;
        element.classList.remove('drop-target');
        element.style.removeProperty('outline');
        element.style.removeProperty('background');
        const sessionKey = e.dataTransfer.getData('application/x-session-key');
        if (sessionKey) {
            e.preventDefault();
            e.stopPropagation();
            const targetGroupId = groupId === '__ungrouped__' ? null : groupId;
            emitWithCsrf('group_move_session', { session_key: sessionKey, group_id: targetGroupId });
        }
    });
}

function setupGroupDropZone(wrapper, groupId) {
    wrapper.addEventListener('dragover', (e) => {
        if (e.dataTransfer.types.includes('application/x-group-id')) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            const rect = wrapper.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            wrapper.classList.remove('drop-above', 'drop-below');
            wrapper.classList.add(e.clientY < midY ? 'drop-above' : 'drop-below');
        }
    });
    wrapper.addEventListener('dragleave', () => wrapper.classList.remove('drop-above', 'drop-below'));
    wrapper.addEventListener('drop', (e) => {
        const draggedGroupId = e.dataTransfer.getData('application/x-group-id');
        wrapper.classList.remove('drop-above', 'drop-below');
        if (draggedGroupId && draggedGroupId !== groupId) {
            e.preventDefault();
            const rect = wrapper.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            const insertBefore = e.clientY < midY;
            reorderGroup(draggedGroupId, groupId, insertBefore);
        }
    });
}

function reorderGroup(draggedId, targetId, insertBefore) {
    const groups = _cachedGroups.map(g => g.id);
    const currentOrder = getGroupOrder();
    // Merge: currentOrder first, then any groups not in currentOrder
    const orderedIds = [...currentOrder.filter(id => groups.includes(id)), ...groups.filter(id => !currentOrder.includes(id))];
    
    const dragIdx = orderedIds.indexOf(draggedId);
    if (dragIdx === -1) return;
    orderedIds.splice(dragIdx, 1);
    
    let targetIdx = orderedIds.indexOf(targetId);
    if (!insertBefore) targetIdx++;
    orderedIds.splice(targetIdx, 0, draggedId);
    
    setGroupOrder(orderedIds);
    emitWithCsrf('get_sessions'); // Refresh to apply new order
}

function makeSessionDraggable(item, sessionKey) {
    item.draggable = true;
    item.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('application/x-session-key', sessionKey);
        e.dataTransfer.effectAllowed = 'move';
        item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => item.classList.remove('dragging'));
}

function updateSessionList(sessions, chatSessions, data) {
    // Cache for live status updates
    _cachedSessions = sessions;
    _cachedChatSessions = chatSessions;
    _cachedData = data;
    _cachedGroups = data.groups || [];
    _cachedSessionGroups = data.session_groups || {};
    
    const sessionList = document.getElementById('sessionList');

    paneController.restore(sessions);

    const inventory = paneController.inventory({ ...data, sessions, chat_sessions: chatSessions });
    const groupsKey = JSON.stringify(_cachedGroups) + '|' + JSON.stringify(_cachedSessionGroups);
    const collapsedKey = JSON.stringify([...getCollapsedParents()].sort());
    const newKey = JSON.stringify(inventory.map(({ adapter, ...session }) => JSON.stringify(adapter.sidebarKey ? adapter.sidebarKey(session) : session)).sort()) + '|' + groupsKey + '|' + collapsedKey;
    if (sessionListInitialized && lastSessionsKey === newKey) return;
    sessionListInitialized = true;
    lastSessionsKey = newKey;

    // Build everything in a fragment to avoid flicker
    const fragment = document.createDocumentFragment();
    
    const allItems = Object.create(null);
    for (const session of inventory) {
        if (session.hidden) continue;
        const element = paneController.sidebarItem(session);
        if (session.pinned) fragment.appendChild(element);
        else allItems[session.key] = { element, groupId: _cachedSessionGroups[session.key] || null };
    }

    // Get local group order, merge with server groups
    const localOrder = getGroupOrder();
    const serverGroupIds = _cachedGroups.map(g => g.id);
    const orderedGroupIds = [
        ...localOrder.filter(id => serverGroupIds.includes(id)),
        ...serverGroupIds.filter(id => !localOrder.includes(id))
    ];
    
    // Render groups (default expanded, URL tracks collapsed)
    let isFirstGroup = true;
    orderedGroupIds.forEach(groupId => {
        const group = _cachedGroups.find(g => g.id === groupId);
        if (!group) return;
        const sessionItems = Object.entries(allItems)
            .filter(([k, v]) => v.groupId === groupId)
            .map(([k, v]) => v.element);
        const groupEl = createGroupElement(group, sessionItems, isGroupExpanded(groupId), isFirstGroup);
        fragment.appendChild(groupEl);
        isFirstGroup = false;
    });

    // Render ungrouped sessions as a collapsible "Other" group
    const ungroupedItems = Object.entries(allItems)
        .filter(([k, v]) => !v.groupId)
        .map(([k, v]) => v.element);
    
    if (ungroupedItems.length > 0) {
        const ungroupedGroup = { id: '__ungrouped__', name: 'Other', color: null };
        // If no real groups exist, Other is the first group
        const ungroupedEl = createGroupElement(ungroupedGroup, ungroupedItems, isGroupExpanded('__ungrouped__'), isFirstGroup);
        fragment.appendChild(ungroupedEl);
    }

    // New Group button at bottom
    const newGroupBtn = document.createElement('div');
    newGroupBtn.className = 'new-group-btn';
    newGroupBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="5" y1="1" x2="5" y2="9"/><line x1="1" y1="5" x2="9" y2="5"/></svg> New Group';
    newGroupBtn.onclick = () => emitWithCsrf('group_create', { name: 'New Group' });
    fragment.appendChild(newGroupBtn);

    // Replace all children at once to avoid flicker
    sessionList.replaceChildren(fragment);

    // Re-highlight both panes
    refreshSidebarHighlights();
    
    // Update pane borders with group colors
    updatePaneBorders();
    
    if (showArchived) {
        document.querySelectorAll('#sessionList > .session-item:not(.archived-item)').forEach(el => el.style.display = 'none');
        document.querySelectorAll('#sessionList > .group-wrapper').forEach(el => el.style.display = 'none');
        document.querySelectorAll('#sessionList > .new-group-btn').forEach(el => el.style.display = 'none');
        emitWithCsrf('acp_list_archived');
    }
}

// Listen for group updates
socket.on('group_created', () => emitWithCsrf('get_sessions'));
socket.on('group_updated', () => emitWithCsrf('get_sessions'));
socket.on('group_deleted', () => emitWithCsrf('get_sessions'));
socket.on('group_archived', data => {
    const members = new Set(data.members);
    for (const pane of [1, 2]) {
        if (members.has(paneController.get(pane).contentKey)) {
            paneController.showTerminal(pane, { clearContent: true, emptyBrowser: true, fit: true });
        }
    }
    updateKbdBtn();
    emitWithCsrf('get_sessions');
});
socket.on('group_restored', data => {
    for (const pane of [1, 2]) {
        if (_cachedSessionGroups[paneController.get(pane).contentKey] === data.group_id) {
            paneController.showTerminal(pane, { clearContent: true, emptyBrowser: true, fit: true });
        }
    }
    updateKbdBtn();
    emitWithCsrf('get_sessions');
});
socket.on('session_group_changed', () => emitWithCsrf('get_sessions'));
socket.on('group_move_failed', data => {
    document.getElementById('groupMoveWarningMessage').textContent = data.message || 'The session could not be moved.';
    document.getElementById('groupMoveWarningModal').classList.add('open');
    document.getElementById('groupMoveWarningClose').focus();
});
socket.on('acp_session_slept', () => emitWithCsrf('get_sessions'));

socket.on('sessions_list', data => { 
    updateSessionList(data.sessions, data.chat_sessions || [], data); 
    // Push group colors to any loaded chat iframes
    broadcastGroupColors();
});

function broadcastGroupColors() {
    paneController.broadcastState();
}

setInterval(() => { emitWithCsrf('get_sessions'); }, 2000);

socket.on('session_created', data => {
    emitWithCsrf('get_sessions');
    if (data.switch && data.name) attachSession(data.name);
});
socket.on('session_renamed', data => {
    for (const state of paneController.panes.values()) {
        if (state.terminalSession === data.old_name) state.terminalSession = data.new_name;
    }
    emitWithCsrf('get_sessions');
    syncUrlParams();
});
socket.on('session_closed', (data) => {
    if (data && data.session) destroyTerm(data.session);
    emitWithCsrf('get_sessions');
});

// --- Attach / Detach ---
function attachSession(sessionName) {
    return paneController.open(sessionName);
}

function mountTerminal({ key: sessionName }) {
    paneController.get(activeTerminal).terminalSession = sessionName;
    sessionStorage.setItem('fernando_session' + activeTerminal, sessionName);
    _paneSession[activeTerminal] = sessionName;
    // If this session was in the other pane, detach the stale viewer to prevent
    // duplicate output (the PTY broadcasts to all viewers on a session).
    const otherPane = activeTerminal === 1 ? 2 : 1;
    if (_paneSession[otherPane] === sessionName) {
        emitWithCsrf('detach_viewer', { terminal: otherPane });
        _paneSession[otherPane] = null;
        paneController.get(otherPane).terminalSession = null;
        sessionStorage.removeItem('fernando_session' + otherPane);
    }
    const entry = showTermInPane(sessionName, activeTerminal);
    // If this session already has a rendered terminal, skip scrollback replay —
    // the content is already in the DOM. We still attach to get live output.
    const skipReplay = entry.ready && !entry.firstAttach;
    entry.firstAttach = false;
    emitWithCsrf('attach_session', { terminal: activeTerminal, session: sessionName, skip_replay: skipReplay });
    return { entry };
}

function getBrowserPaneSession(pane) {
    return paneController.browserKey(pane);
}

function syncUrlParams() {
    if (!window._urlParamsProcessed) return;
    const params = new URLSearchParams(window.location.search);
    // Clear session-related params, preserve others (like collapsed)
    params.delete('session');
    params.delete('session2');
    params.delete('split');
    params.delete('active');
    const s1 = paneController.locationKey(1);
    const s2 = paneController.locationKey(2);
    if (s1) params.set('session', s1);
    if (isSplit && s2) { params.set('session2', s2); params.set('split', '1'); params.set('active', String(activeTerminal)); }
    const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    try { history.replaceState(null, '', newUrl); } catch(e) {}
    // Notify backend which chat sessions are in active panes (for idle protection)
    syncActiveChatPanes();
}

// Notify chat iframes which pane is active (for mark-read logic)
function broadcastPaneActive() {
    paneController.broadcastState();
}

// Click handlers
function syncPaneSidebar(paneNum) {
    const s = paneController.sessionKey(paneNum);
    if (s) {
        highlightSidebarItem(s);
        // Retry in case sidebar is rebuilding
        setTimeout(() => highlightSidebarItem(s), 200);
    } else {
        document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
    }
}
function activatePane1() {
    setActiveTerminal(1, true);
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
}
function activatePane2() {
    if (isSplit) {
        setActiveTerminal(2, true);
    }
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
}
document.getElementById('terminal1-container').addEventListener('mousedown', activatePane1);
document.getElementById('terminal1-container').addEventListener('touchstart', activatePane1, { passive: true });
document.getElementById('terminal2-container').addEventListener('mousedown', activatePane2);
document.getElementById('terminal2-container').addEventListener('touchstart', activatePane2, { passive: true });

// iframe focus detection handled by container touchstart/mousedown handlers

function navigateJupyter(pane, location) {
    const state = paneController.get(pane);
    const oldPath = state.jupyterPath;
    if (location.path) {
        state.jupyterPath = '/jupyter' + location.path.replace(/^\/jupyter/, '');
        _jupyterNamePaths[location.name] = state.jupyterPath;
    }
    const oldName = state.contentKey.slice(8);
    if (oldName === location.name) {
        if (state.jupyterPath !== oldPath) syncUrlParams();
        return;
    }
    state.contentKey = 'jupyter:' + location.name;
    const item = [...document.querySelectorAll('.session-item')].find(item => item.dataset.session === 'jupyter:' + oldName);
    if (item) {
        item.dataset.session = state.contentKey;
        const label = item.querySelector('.session-name');
        if (label) {
            label.innerHTML = paneController.sessionTypes.get('jupyter').sidebar().icon;
            label.appendChild(document.createTextNode(location.name));
        }
    }
    const groupId = _cachedSessionGroups['jupyter:' + oldName];
    emitWithCsrf('close_jupyter', { name: oldName, preserve_group: true });
    emitWithCsrf('open_jupyter', { name: location.name, group_id: groupId });
    refreshSidebarHighlights();
    syncUrlParams();
}

// --- New Session Modal ---
let newSessionTargetGroupId = null;

function openNewSessionModal(targetGroupId = null) {
    newSessionTargetGroupId = targetGroupId;
    document.getElementById('newSessionModal').classList.add('open');
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
}
function closeNewSessionModal() { 
    document.getElementById('newSessionModal').classList.remove('open');
    newSessionTargetGroupId = null;
}
function getNewSessionGroupId() {
    if (newSessionTargetGroupId === '__ungrouped__') return null;
    return newSessionTargetGroupId || (typeof getActivePaneGroupId === 'function' ? getActivePaneGroupId() : null);
}
function createSessionType(type) { 
    const groupId = getNewSessionGroupId();
    emitWithCsrf('create_session', { type: type, group_id: groupId }); 
    closeNewSessionModal(); 
}
function closeSession(event, sessionName) {
    event.stopPropagation();
    paneController.close(sessionName);
}
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (window.innerWidth <= 500) {
        sidebar.classList.toggle('open');
    } else {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('fernando_sidebar_collapsed', sidebar.classList.contains('collapsed') ? '1' : '');
    }
}

// Restore sidebar state on desktop
if (window.innerWidth > 500 && localStorage.getItem('fernando_sidebar_collapsed') === '1') {
    document.getElementById('sidebar').classList.add('collapsed');
}

bindBackdropDismissal(document, target => {
    const sidebar = document.getElementById('sidebar');
    const sidebarToggle = document.querySelector('.sidebar-toggle');
    return target !== null && window.innerWidth <= 500 && sidebar.classList.contains('open')
        && !sidebar.contains(target) && !sidebarToggle.contains(target);
}, () => document.getElementById('sidebar').classList.remove('open'));

// --- Initial load ---
emitWithCsrf('get_sessions');
