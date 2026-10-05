paneController.register('desktop', {
    mobileClass: 'desktop-active',
    keyboardButton: true,
    sendKey: (key, desktopKey) => emitWithCsrf('desktop_key', { key: desktopKey }),
    dismissSidebarOnActivate: true,
    sidebarOrder: 0,
    matches: key => key === 'desktop',
    ownsKey: () => false,
    urlFragment: '/kasm/',
    context: () => ({ type: 'desktop' }),
    contextWithoutKey: true,
    keyFromUrl: url => url.includes('/kasm/') ? 'desktop' : null,
    prepare: () => ({ key: null }),
    toggle: { clearLocation: true, fit: true },
    mount({ browser, state }) {
        state.jupyterPath = null;
        browser.innerHTML = '';
        ensureDesktopIframe(browser);
    },
    sessions: () => [{ key: 'desktop', name: 'Desktop', pinned: true }],
    skipSelectionHighlight: true,
    sidebar: () => ({ icon: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px"><rect x="1" y="2" width="14" height="10" rx="1"/><line x1="5" y1="14" x2="11" y2="14"/><line x1="8" y1="12" x2="8" y2="14"/></svg>', button: { icon: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 8a6 6 0 0 1 10.3-4.1"/><path d="M14 8a6 6 0 0 1-10.3 4.1"/><polyline points="2 2 2 6 6 6"/><polyline points="14 14 14 10 10 10"/></svg>', run: () => restartDesktop() } }),
});

paneController.register('jupyter', {
    mobileClass: '',
    dismissSidebarOnActivate: true,
    navigate: (pane, location) => navigateJupyter(pane, location),
    sidebarOrder: 1,
    matches: key => key === 'jupyter' || key.startsWith('jupyter:'),
    ownsKey: key => key.startsWith('jupyter:'),
    urlFragment: '/jupyter/',
    context: key => ({ type: 'jupyter', notebook: key.slice(8) }),
    keyFromUrl: url => url.includes('/jupyter/') ? 'jupyter:Jupyter' : null,
    prepare(key) {
        const groupId = getNewSessionGroupId();
        closeNewSessionModal();
        const name = key === 'jupyter' ? 'Jupyter-' + (++_jupyterCounter) : key.slice(8);
        return { key: 'jupyter:' + name, name, groupId };
    },
    mount: context => mountJupyter(context),
    afterOpen({ name, groupId }) {
        emitWithCsrf('open_jupyter', { name, group_id: groupId });
        emitWithCsrf('get_sessions');
    },
    activateExisting: true,
    selectionKey: key => 'jupyter:' + (_jupyterNamePaths[key.slice(8)] || key.slice(8)),
    dismiss: { clearLocation: true, emptyBrowser: true, fit: true },
    close(key) {
        emitWithCsrf('close_jupyter', { name: key.slice(8) });
        emitWithCsrf('get_sessions');
    },
    syncOnClose: true,
    sessions: data => [...(data.running_jupyter || [])].sort().map(name => ({ key: 'jupyter:' + name, name })),
    sidebar: () => ({ icon: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:-1px;margin-right:4px"><path d="M7.157 22.201A1.784 1.799 0 0 1 5.374 24a1.784 1.799 0 0 1-1.784-1.799 1.784 1.799 0 0 1 1.784-1.799 1.784 1.799 0 0 1 1.783 1.799zM20.582 1.427a1.415 1.427 0 0 1-1.415 1.428 1.415 1.427 0 0 1-1.416-1.428A1.415 1.427 0 0 1 19.167 0a1.415 1.427 0 0 1 1.415 1.427zM4.992 3.336A1.047 1.056 0 0 1 3.946 4.39a1.047 1.056 0 0 1-1.047-1.055A1.047 1.056 0 0 1 3.946 2.28a1.047 1.056 0 0 1 1.046 1.056zm7.336 1.517c3.769 0 7.06 1.38 8.768 3.424a9.363 9.363 0 0 0-3.393-4.547 9.238 9.238 0 0 0-5.377-1.728A9.238 9.238 0 0 0 6.95 3.73a9.363 9.363 0 0 0-3.394 4.547c1.713-2.04 5.004-3.424 8.772-3.424zm.001 13.295c-3.768 0-7.06-1.381-8.768-3.425a9.363 9.363 0 0 0 3.394 4.547A9.238 9.238 0 0 0 12.33 21a9.238 9.238 0 0 0 5.377-1.729 9.363 9.363 0 0 0 3.393-4.547c-1.712 2.044-5.003 3.425-8.772 3.425Z"/></svg>' }),
});

paneController.register('notebook', {
    ready: ({ browser, key }) => ensureNotebookIframe(browser, key.slice(9)),
    firstReadyOnly: true,
    mobileClass: 'notes-active',
    dismissSidebarOnActivate: true,
    sidebarOrder: 2,
    matches: key => key.startsWith('notebook:'),
    ownsKey: key => key.startsWith('notebook:'),
    urlFragment: '/notes/',
    context: key => ({ type: 'notebook', notebook: key.slice(9) }),
    keyFromUrl: url => {
        const match = url.match(/\/notes\/([^/?#]+)\//);
        return match ? 'notebook:' + match[1] : null;
    },
    prepare(key) {
        const groupId = _cachedSessionGroups[key] ? null : (window._notebookTargetGroupId || getActivePaneGroupId());
        window._notebookTargetGroupId = null;
        return { key, groupId };
    },
    mount({ browser }) {
        browser.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#5a9fd4;font-family:sans-serif">Starting notebook...</div>';
    },
    afterOpen({ key, groupId }) {
        emitWithCsrf('start_notebook', { name: key.slice(9), group_id: groupId });
        emitWithCsrf('get_sessions');
    },
    dismiss: key => ({ matches: pane => paneController.get(pane).contentKey === key.slice(9), emptyBrowser: true, fit: true }),
    close(key) {
        emitWithCsrf('stop_notebook', { name: key.slice(9) });
        emitWithCsrf('get_sessions');
    },
    syncOnClose: true,
    sessions: data => [...(data.running_notebooks || [])].sort().map(name => ({ key: 'notebook:' + name, name })),
    sidebar: () => ({ icon: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-1px;margin-right:4px"><rect x="3" y="1" width="10" height="14" rx="1"/><line x1="6" y1="1" x2="6" y2="15"/><line x1="1" y1="4" x2="3" y2="4"/><line x1="1" y1="8" x2="3" y2="8"/><line x1="1" y1="12" x2="3" y2="12"/></svg>' }),
});

paneController.register('chat', {
    sidebarKey: session => [session.key, session.name, !!session.loaded, session.status || 'idle'],
    syncOnClose: false,
    closeLabel: 'Archive',
    describeSession: session => session.key + (session.loaded === false ? ' (sleeping)' : ''),
    sidebarOrder: 4,
    matches: key => key.startsWith('chat:'),
    ownsKey: key => key.startsWith('chat:'),
    urlFragment: '/chat/',
    context: () => ({ type: 'chat' }),
    contextWithoutKey: true,
    keyFromUrl: url => {
        const match = url.match(/\/chat\/([^/?#]+)/);
        return match ? 'chat:' + match[1] : null;
    },
    controlsBeforeUrl: true,
    mount: context => mountChat(context),
    selectInactiveOnly: true,
    closeBeforeDismiss: true,
    close: key => emitWithCsrf('acp_close', { session_id: key.slice(5) }),
    dismiss: key => ({
        matches: pane => paneController.elements(pane).browser.querySelector('iframe')?.src.includes('/chat/' + key.slice(5)),
        clearContent: false, emptyBrowser: true, fit: true,
    }),
    sessions: data => sortChatSessions(data.chat_sessions || []).map(chat => ({ ...chat, key: 'chat:' + chat.id })),
    sidebar: session => chatSidebarDescriptor(session),
});

paneController.register('terminal', {
    focusOnTap: pane => getTermForPane(pane)?.focus(),
    dismiss: null,
    syncOnClose: false,
    mobileClass: '',
    priority: -1,
    requiresAvailable: true,
    sidebarOrder: 3,
    surface: 'terminal',
    matches: key => !key.includes(':'),
    context: key => ({ type: 'terminal', session: key }),
    prepare(key) {
        const current = paneController.get(activeTerminal);
        if (current.surface === 'terminal' && current.terminalSession === key) {
            if (isSplit && _lastDirectPaneTarget !== activeTerminal && Date.now() - _lastDirectPaneTouch < 5000) {
                setActiveTerminal(_lastDirectPaneTarget, true);
            } else {
                highlightSidebarItem(key);
                return false;
            }
        }
        return { key };
    },
    beforeMount: () => updateKbdBtn(),
    mount: context => mountTerminal(context),
    selectInactiveOnly: true,
    close(key) {
        showConfirm(`Close session "${key}"?`).then(result => {
            if (result) emitWithCsrf('close_session', { session: key });
        });
    },
    sessions: data => (data.sessions || []).map(name => ({ key: name, name })),
    sidebar: session => ({
        icon: session.name.startsWith('Shell') ? '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px"><polyline points="2 4 6 8 2 12"/><line x1="8" y1="12" x2="14" y2="12"/></svg>' : session.name.startsWith('Kiro') ? '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px"><circle cx="8" cy="5" r="3"/><path d="M3 14c0-3 2-5 5-5s5 2 5 5"/></svg>' : '',
        rename: name => emitWithCsrf('rename_session', { old_name: session.name, new_name: name }),
    }),
});

function sortChatSessions(chats) {
    const result = [];
    const children = new Map();
    const roots = [];
    for (const chat of chats) {
        if (chat.parent_id) {
            if (!children.has(chat.parent_id)) children.set(chat.parent_id, []);
            children.get(chat.parent_id).push(chat);
        } else roots.push(chat);
    }
    const collapsed = getCollapsedParents();
    const visited = new Set();
    const add = (chat, parentCollapsed = false) => {
        visited.add(chat.id);
        const descendants = children.get(chat.id) || [];
        chat._hasChildren = descendants.length > 0;
        chat._isCollapsed = collapsed.has(chat.id);
        chat.hidden = parentCollapsed;
        result.push(chat);
        for (const child of descendants) {
            child._isChild = true;
            add(child, parentCollapsed || chat._isCollapsed);
        }
    };
    roots.forEach(chat => add(chat));
    for (const chat of chats) {
        if (!visited.has(chat.id)) {
            chat._isChild = !!chat.parent_id;
            result.push(chat);
        }
    }
    return result;
}

function chatSidebarDescriptor(chat) {
    const groupId = _cachedSessionGroups[chat.key];
    const group = groupId ? _cachedGroups.find(group => group.id === groupId) : null;
    const color = group ? group.color : '#4b8ce0';
    const highlighted = chat.loaded && ['working', 'unread'].includes(chat.status);
    const fill = !chat.loaded ? 'none' : highlighted ? color : 'currentColor';
    const stroke = highlighted ? color : 'currentColor';
    const iconClass = chat.loaded && chat.status === 'working' ? 'chat-icon-working' : '';
    return {
        className: 'chat-session-item',
        dataset: { treeId: chat.id, ...(chat.parent_id ? { treeParent: chat.parent_id } : {}) },
        labelClass: chat._isChild ? 'child-session' : '',
        icon: '<svg class="chat-icon ' + iconClass + '" width="12" height="12" viewBox="0 0 16 16" fill="' + fill + '" stroke="' + stroke + '" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px"><path d="M2 3h12v8H6l-4 3V3z"/></svg>',
        label() {
            const element = document.createElement('span');
            const sidebarLabel = chatSidebarLabel(chat);
            if (sidebarLabel.prefix) {
                const prefix = document.createElement('span');
                prefix.className = 'session-origin-prefix';
                prefix.textContent = sidebarLabel.prefix + ' ';
                element.appendChild(prefix);
            }
            const title = document.createElement('span');
            title.textContent = sidebarLabel.name;
            element.appendChild(title);
            return { element, title };
        },
        closeLabel: 'Archive',
        optimisticRename: true,
        rename: name => emitWithCsrf('acp_rename', { session_id: chat.id, name }),
        sleep: () => emitWithCsrf('acp_sleep', { session_id: chat.id }),
        clone: () => emitWithCsrf('acp_clone', { session_id: chat.id, group_id: groupId }),
        collapse: chat._hasChildren ? { isCollapsed: chat._isCollapsed, toggle: () => toggleParentExpanded(chat.id) } : null,
    };
}
