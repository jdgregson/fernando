async function fileSessionRequest(data) {
    const response = await fetch('/api/file-browser/sessions', {
        method: 'POST', headers: {'X-API-Key': window.FERNANDO_API_KEY, 'Content-Type': 'application/json'}, body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'File browser session request failed');
    emitWithCsrf('get_sessions');
    return result;
}

function createFileSession() {
    const groupId = getNewSessionGroupId();
    closeNewSessionModal();
    fileSessionRequest({group_id: groupId}).then(session => paneController.open('files:' + session.id)).catch(error => showAlert(error.message));
}

function fileSessionPresentation(session) {
    const key = 'files:' + session.id;
    const active = paneController.get(activeTerminal);
    const path = active.surface === 'browser' && active.contentKey === key && active.location
        ? active.location : sessionStorage.getItem('file-location:last:' + key) || session.home;
    const display = path === session.home ? '~' : path?.startsWith(session.home + '/') ? '~' + path.slice(session.home.length) : path;
    const characters = Array.from(display || session.name);
    return {...session, key, path, name: characters.length > 50 ? '…' + characters.slice(-50).join('') : characters.join('')};
}

paneController.register('files', {
    sidebarOrder: 2.5,
    selectInactiveOnly: true,
    matches: key => key.startsWith('files:'),
    urlFragment: '/files/',
    sessions: data => (data.file_sessions || []).map(fileSessionPresentation),
    prepare(key) {
        const separator = key.indexOf('?');
        const identity = separator < 0 ? key : key.slice(0, separator);
        const location = separator < 0 ? sessionStorage.getItem('file-location:' + activeTerminal + ':' + identity) : new URLSearchParams(key.slice(separator + 1)).get('path');
        return {key: identity, location};
    },
    mount({key, location, browser, state}) {
        state.location = location;
        const query = new URLSearchParams({api_key: window.FERNANDO_API_KEY});
        if (location) query.set('path', location);
        const frame = document.createElement('iframe');
        frame.src = '/files/' + encodeURIComponent(key.slice(6)) + '?' + query;
        frame.style.cssText = 'width:100%;height:100%;border:none';
        browser.replaceChildren(frame);
    },
    serialize: (key, state) => state.location ? key + '?' + new URLSearchParams({path: state.location}) : key,
    navigate(pane, location) {
        if (!location.path.startsWith('/')) return null;
        sessionStorage.setItem('file-location:' + pane + ':' + paneController.get(pane).contentKey, location.path);
        sessionStorage.setItem('file-location:last:' + paneController.get(pane).contentKey, location.path);
        emitWithCsrf('get_sessions');
        return {location: location.path};
    },
    context: (key, state) => ({type: 'files', description: 'File browser' + (state.location ? ' at ' + state.location : '')}),
    close: key => fileSessionRequest({id: key.slice(6)}).catch(error => showAlert(error.message)),
    sidebar: session => ({
        className: 'file-session-item',
        title: session.path || session.name,
        icon: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px"><path d="M3 5h7l2 3h9v12H3z"/></svg>',
        label() {
            const title = document.createElement('span');
            title.textContent = session.name;
            title.title = session.path || session.name;
            return {element: title, title};
        },
    }),
});
