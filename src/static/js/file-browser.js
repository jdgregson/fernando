(() => {
    const $ = id => document.getElementById(id);
    const apiKey = document.body.dataset.apiKey;
    const home = document.body.dataset.home;
    const touchUI = matchMedia('(pointer: coarse)').matches;
    const params = new URLSearchParams(location.search);
    let directory = params.get('path') || home;
    let entries = [];
    let selected = null;
    let sort = 'name';
    let descending = false;
    let showHidden = document.body.dataset.defaultHidden === 'true';
    let tileMode = document.body.dataset.defaultView === 'tile';
    const selectedPaths = new Set();
    let visibleEntries = [];
    let selectionAnchor = null;
    let multiMode = false;
    let lastEntryClick = null;
    let renameTimer = null;
    let renaming = null;
    let entryHold = null;
    let entryHoldTimer = null;
    let suppressEntryClick = false;
    let marquee = null;
    let marqueeFrame = null;
    let dragId = null;
    let editingPath = false;
    let pathGesture = null;
    let pathHoldTimer = null;
    let suppressPathClick = false;
    let statusTimer = null;
    let generation = 0;
    let reportedLocation = null;
    let opened = null;
    let dirty = false;
    let loadingEditor = false;
    let propertiesRequest = 0;
    const editor = ace.edit('editor', {theme: 'ace/theme/tomorrow_night', fontSize: 13, showPrintMargin: false, useSoftTabs: true, tabSize: 4});
    ace.config.set('basePath', '/static/vendor/ace');
    editor.session.setUseWorker(false);
    editor.session.on('change', () => {
        if (!loadingEditor && opened?.kind === 'text') {
            dirty = true;
            $('dirtyMark').hidden = false;
        }
    });
    function status(text, error = false) {
        clearTimeout(statusTimer);
        $('status').textContent = text;
        $('statusPopup').classList.toggle('error', error);
        $('statusPopup').hidden = false;
        if (!error) statusTimer = setTimeout(() => { $('statusPopup').hidden = true; }, 4000);
    }
    function setMenu(open) {
        hideContext();
        document.body.classList.toggle('menu-open', open);
        $('browserMenu').inert = !open;
        $('browserMenu').setAttribute('aria-hidden', String(!open));
        $('menuBackdrop').hidden = !open;
        $('menuButton').setAttribute('aria-expanded', String(open));
    }
    function menuAction(action) {
        return () => { hideContext(); run(action); };
    }
    function drawerAction(action) {
        return () => { if (touchUI) setMenu(false); run(action); };
    }
    function editPath() {
        if (touchUI) setMenu(false);
        editingPath = true;
        $('pathDisplay').hidden = true;
        $('pathForm').hidden = false;
        $('pathInput').value = directory;
        FernandoPane.activate();
        $('pathInput').focus({preventScroll: true});
        $('pathInput').select();
    }
    function finishPathEdit() {
        editingPath = false;
        $('pathForm').hidden = true;
        $('pathDisplay').hidden = false;
        $('pathInput').value = directory;
        $('pathDisplay').scrollLeft = $('pathDisplay').scrollWidth;
    }
    function resetPathGesture() {
        pathGesture = null;
        clearTimeout(pathHoldTimer);
        $('pathDisplay').classList.remove('hold-ready');
    }
    function renderPath() {
        const crumbs = document.createDocumentFragment();
        const inHome = directory === home || directory.startsWith(home + '/');
        const parts = inHome ? ['~', ...directory.slice(home.length).split('/').filter(Boolean)] : ['/', ...directory.split('/').filter(Boolean)];
        let current = '';
        for (const [index, part] of parts.entries()) {
            current = index === 0 ? inHome ? home : '/' : current.replace(/\/$/, '') + '/' + part;
            if (index > 0 && !(index === 1 && parts[0] === '/')) {
                const separator = document.createElement('span'); separator.textContent = '/'; crumbs.appendChild(separator);
            }
            const link = document.createElement('a');
            const target = new URL(location.href);
            target.searchParams.set('path', current);
            link.href = target.href;
            link.dataset.path = current;
            link.textContent = part;
            link.title = current;
            if (index === parts.length - 1) link.setAttribute('aria-current', 'page');
            crumbs.appendChild(link);
        }
        $('breadcrumbs').replaceChildren(crumbs);
        requestAnimationFrame(() => { if (!editingPath) $('pathDisplay').scrollLeft = $('pathDisplay').scrollWidth; });
    }
    async function api(path, body, method = body ? 'POST' : 'GET') {
        const headers = {'X-API-Key': apiKey};
        const options = {method, headers};
        if (body instanceof FormData) options.body = body;
        else if (body) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
        const response = await fetch('/api/file-browser/' + path, options);
        if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('File request failed (HTTP ' + response.status + ')');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Request failed: ' + response.status);
        return data;
    }
    function run(action) {
        Promise.resolve().then(action).catch(error => status(error.message, true));
    }
    function url(endpoint, path, inline = false) {
        return '/api/file-browser/' + endpoint + '?' + new URLSearchParams({path, api_key: apiKey, ...(inline ? {inline: '1'} : {})});
    }
    function size(bytes) {
        if (bytes < 1024) return bytes + ' B';
        const power = Math.min(4, Math.floor(Math.log(bytes) / Math.log(1024)));
        return (bytes / 1024 ** power).toFixed(1) + ' ' + ['B', 'KiB', 'MiB', 'GiB', 'TiB'][power];
    }
    function prompt(title, description, value = null, danger = false) {
        $('promptTitle').textContent = title;
        $('promptDescription').textContent = description;
        $('promptInput').hidden = value === null;
        $('promptInput').value = value || '';
        $('promptInput').required = value !== null;
        $('promptSubmit').classList.toggle('danger', danger);
        $('promptSubmit').textContent = title;
        $('promptDialog').returnValue = '';
        $('promptDialog').showModal();
        if (value !== null) { $('promptInput').focus(); $('promptInput').select(); }
        return new Promise(resolve => $('promptDialog').addEventListener('close', () => resolve($('promptDialog').returnValue === 'ok' ? value === null ? true : $('promptInput').value : null), {once: true}));
    }
    async function showProperties() {
        const path = selectedPaths.size === 1 ? selected.path : directory;
        const requestId = ++propertiesRequest;
        const dialog = $('propertiesDialog');
        const content = $('propertiesContent');
        $('propertiesTitle').textContent = 'Properties';
        content.textContent = 'Loading…';
        dialog.showModal();
        const data = await api('properties?' + new URLSearchParams({path})).catch(error => {
            if (requestId === propertiesRequest && dialog.open) content.textContent = error.message;
            throw error;
        });
        if (requestId !== propertiesRequest || !dialog.open) return;
        $('propertiesTitle').textContent = data.name + ' — Properties';
        content.replaceChildren();
        const bytes = value => size(value) + ' (' + value.toLocaleString() + ' bytes)';
        const date = value => value === null ? 'Not available' : new Date(value * 1000).toLocaleString(undefined, {timeZone: 'America/Los_Angeles', year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false, timeZoneName:'short'});
        const section = (title, rows) => {
            const element = document.createElement('section'); element.className = 'properties-section';
            const heading = document.createElement('h3'); heading.textContent = title;
            const grid = document.createElement('dl'); grid.className = 'properties-grid';
            for (const [label, value] of rows) {
                const term = document.createElement('dt'); term.textContent = label;
                const detail = document.createElement('dd'); detail.textContent = value;
                grid.append(term, detail);
            }
            element.append(heading, grid); content.appendChild(element);
            return element;
        };
        const general = [['Name', data.name], ['Type', data.type], ['Location', data.location], ['Full path', data.path]];
        if (data.mime) general.push(['MIME type', data.mime]);
        general.push([data.directory ? 'Directory entry size' : 'Size', bytes(data.size)], [data.directory ? 'Entry size on disk' : 'Size on disk', bytes(data.allocated)], ['Hidden', data.hidden ? 'Yes' : 'No']);
        if (data.target !== null) general.push(['Link target', data.target]);
        section('General', general);
        section('Timestamps', [['Created', date(data.created)], ['Modified', date(data.modified)], ['Accessed', date(data.accessed)], ['Metadata changed', date(data.changed)]]);
        section('Ownership and permissions', [['Owner', (data.owner || 'Unknown user') + ' (UID ' + data.uid + ')'], ['Group', (data.group || 'Unknown group') + ' (GID ' + data.gid + ')'], ['Permissions', data.mode + ' (' + data.permissions + ')'], ['Owner access', data.mode.slice(1, 4)], ['Group access', data.mode.slice(4, 7)], ['Other access', data.mode.slice(7, 10)]]);
        section('Filesystem', [['Inode', data.inode], ['Device ID', data.device], ['Hard links', data.links]]);
        if (data.target !== null) {
            const note = document.createElement('p'); note.textContent = 'These properties describe the symbolic link itself.'; content.appendChild(note);
        }
        if (data.directory) {
            const pending = document.createElement('p'); pending.textContent = 'Calculating folder contents…'; content.appendChild(pending);
            const totals = await api('directory-totals?' + new URLSearchParams({path})).catch(error => {
                if (requestId === propertiesRequest && dialog.open) pending.textContent = 'Could not calculate folder contents: ' + error.message;
                throw error;
            });
            if (requestId !== propertiesRequest || !dialog.open) return;
            pending.remove();
            section(totals.complete ? 'Folder contents' : 'Folder contents — partial (scan limit reached)', [['Contains', totals.files.toLocaleString() + ' files, ' + totals.directories.toLocaleString() + ' folders, ' + totals.symlinks.toLocaleString() + ' links, ' + totals.other.toLocaleString() + ' other items'], ['Content size', bytes(totals.size)], ['Total size on disk', bytes(totals.allocated)]]);
            const note = document.createElement('p'); note.textContent = 'Includes hidden items. Symbolic links are not followed; hard-linked data is counted once.'; content.appendChild(note);
        }
    }
    async function leaveEditor() {
        return !dirty || await prompt('Discard edits', 'This file has unsaved changes. Discard them?', null, true);
    }
    function refreshSelection() {
        const available = new Set(entries.map(entry => entry.path));
        for (const path of selectedPaths) if (!available.has(path)) selectedPaths.delete(path);
        if (!selected || !selectedPaths.has(selected.path)) selected = entries.find(entry => selectedPaths.has(entry.path)) || null;
        const count = selectedPaths.size;
        $('selectionName').textContent = count === 1 ? selected.name : count + ' items selected';
        $('selectionName').title = count === 1 ? selected.path + ' · ' + selected.mode : '';
        $('openButton').disabled = $('renameButton').disabled = count !== 1;
        $('propertiesButton').disabled = count > 1;
        for (const id of ['copyButton', 'moveButton', 'copyNameButton', 'deleteButton']) $(id).disabled = count === 0;
        $('downloadButton').disabled = !count || entries.some(entry => selectedPaths.has(entry.path) && entry.directory);
        $('copyNameLabel').textContent = count > 1 ? 'Copy file names' : 'Copy file name';
        $('selectionModeButton').hidden = !multiMode && count < 2;
        $('selectionCount').textContent = multiMode ? 'Done · ' + count : count + ' selected';
        document.body.classList.toggle('selecting', multiMode);
        $('selectItemsButton').setAttribute('aria-pressed', String(multiMode));
        $('selectItemsLabel').textContent = multiMode ? 'Exit selection mode' : 'Select items';
        for (const row of $('fileList').querySelectorAll('[data-path]')) {
            const active = selectedPaths.has(row.dataset.path);
            row.classList.toggle('selected', active);
            row.setAttribute('aria-selected', String(active));
        }
    }
    function select(entry, {toggle = false, range = false} = {}) {
        clearTimeout(renameTimer);
        lastEntryClick = null;
        if (range && selectionAnchor) {
            const start = visibleEntries.findIndex(item => item.path === selectionAnchor);
            const end = visibleEntries.findIndex(item => item.path === entry.path);
            if (!toggle) selectedPaths.clear();
            if (start >= 0 && end >= 0) for (const item of visibleEntries.slice(Math.min(start, end), Math.max(start, end) + 1)) selectedPaths.add(item.path);
        } else {
            if (!toggle) selectedPaths.clear();
            if (entry) {
                if (toggle && selectedPaths.has(entry.path)) selectedPaths.delete(entry.path);
                else selectedPaths.add(entry.path);
                selectionAnchor = entry.path;
            }
        }
        selected = entry && selectedPaths.has(entry.path) ? entry : null;
        refreshSelection();
    }
    function hideContext() {
        $('fileContextMenu').hidden = true;
    }
    function showContext(entry, x, y) {
        clearTimeout(renameTimer);
        cancelRename();
        if (entry && !selectedPaths.has(entry.path)) select(entry);
        else { if (entry) selected = entry; refreshSelection(); }
        if (touchUI) setMenu(false);
        const menu = $('fileContextMenu');
        menu.hidden = false;
        menu.style.left = Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8)) + 'px';
        menu.style.top = Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8)) + 'px';
    }
    function resetEntryHold() {
        clearTimeout(entryHoldTimer);
        entryHold = null;
    }
    function cancelRename() {
        clearTimeout(renameTimer);
        if (!renaming || renaming.saving) return;
        const current = renaming;
        renaming = null;
        current.input.replaceWith(current.label);
        current.row.draggable = !touchUI;
    }
    function beginRename(entry) {
        if (!entry || renaming) return;
        hideContext();
        const row = [...$('fileList').querySelectorAll('[data-path]')].find(element => element.dataset.path === entry.path);
        if (!row) return;
        const label = row.querySelector('.entry-name');
        const input = document.createElement('input');
        input.className = 'file-rename'; input.value = entry.name; input.setAttribute('aria-label', 'Rename ' + entry.name);
        input.autocomplete = 'off'; input.spellcheck = false;
        const state = {input, label, entry, row, saving: false};
        renaming = state;
        row.draggable = false;
        label.replaceWith(input);
        input.focus({preventScroll: true});
        const extension = entry.name.lastIndexOf('.');
        input.setSelectionRange(0, !entry.directory && extension > 0 ? extension : entry.name.length);
        const commit = () => {
            if (renaming !== state || state.saving) return;
            const name = input.value;
            if (name === entry.name) { cancelRename(); return; }
            if (!name || name === '.' || name === '..' || name.includes('/') || name.includes('\0')) { status('Enter a valid file name without slashes', true); return; }
            const destination = entry.path.slice(0, entry.path.lastIndexOf('/') + 1) + name;
            state.saving = true; input.disabled = true;
            api('operation', {action:'move', path:entry.path, destination}).then(() => {
                selectedPaths.delete(entry.path); selectedPaths.add(destination);
                renaming = null; lastEntryClick = null;
                return list();
            }).catch(error => { state.saving = false; input.disabled = false; status(error.message, true); });
        };
        input.addEventListener('click', event => event.stopPropagation());
        input.addEventListener('pointerdown', event => event.stopPropagation());
        input.addEventListener('dblclick', event => event.stopPropagation());
        input.addEventListener('keydown', event => {
            event.stopPropagation();
            if (event.key === 'Enter') { event.preventDefault(); commit(); }
            if (event.key === 'Escape') { event.preventDefault(); cancelRename(); }
        });
        input.addEventListener('blur', commit);
    }
    function entryClick(entry, event) {
        if (suppressEntryClick) { suppressEntryClick = false; event.preventDefault(); event.stopPropagation(); return; }
        if (renaming) return;
        hideContext();
        const wasSelected = selectedPaths.size === 1 && selectedPaths.has(entry.path);
        const now = performance.now();
        const slowSecondClick = wasSelected && lastEntryClick?.path === entry.path && now - lastEntryClick.time >= 600;
        const toggle = multiMode || event.ctrlKey || event.metaKey || !!event.target.closest('.selection-check');
        select(entry, {toggle, range:event.shiftKey});
        lastEntryClick = {path:entry.path, time:now};
        $('fileList').focus({preventScroll:true});
        if (slowSecondClick && !toggle && !event.shiftKey && event.detail < 2) {
            if (touchUI) beginRename(entry);
            else renameTimer = setTimeout(() => beginRename(entry), 350);
        }
    }
    function bindEntry(row, entry) {
        row.draggable = !touchUI;
        row.addEventListener('click', event => entryClick(entry, event));
        row.addEventListener('dblclick', event => {
            clearTimeout(renameTimer);
            if (!renaming && !multiMode && !event.ctrlKey && !event.metaKey && !event.shiftKey) run(() => openEntry(entry));
        });
        row.addEventListener('contextmenu', event => {
            if (event.target.closest('input')) return;
            event.preventDefault(); resetEntryHold();
            if (event.pointerType === 'touch') suppressEntryClick = true;
            showContext(entry, event.clientX, event.clientY);
        });
        row.addEventListener('pointerdown', event => {
            suppressEntryClick = false;
            resetEntryHold();
            if (event.pointerType !== 'touch' || event.isPrimary === false) return;
            entryHold = {id:event.pointerId, x:event.clientX, y:event.clientY};
            entryHoldTimer = setTimeout(() => {
                suppressEntryClick = true;
                showContext(entry, event.clientX, event.clientY);
                entryHold = null;
            }, 500);
        });
        row.addEventListener('pointermove', event => { if (entryHold && Math.hypot(event.clientX-entryHold.x, event.clientY-entryHold.y) > 8) resetEntryHold(); });
        row.addEventListener('pointerup', resetEntryHold);
        row.addEventListener('pointercancel', resetEntryHold);
        row.addEventListener('dragstart', event => {
            if (event.target.closest('input') || renaming) return;
            clearTimeout(renameTimer); resetEntryHold(); hideContext();
            if (!selectedPaths.has(entry.path)) select(entry);
            try {
                dragId = FernandoFileDrag.create(entries.filter(item => selectedPaths.has(item.path)));
                event.dataTransfer.setData(FernandoFileDrag.type, dragId);
                event.dataTransfer.effectAllowed = 'copyMove';
            } catch (error) {
                event.preventDefault();
                status(error.message, true);
            }
        });
        row.addEventListener('dragend', () => {
            FernandoFileDrag.discard(dragId);
            dragId = null; clearDropTarget();
        });
    }
    function clearSearch(collapse = false) {
        $('filterInput').value = '';
        $('menuButton').classList.remove('filtered');
        if (collapse) { $('searchField').hidden = true; $('searchButton').setAttribute('aria-expanded', 'false'); }
    }
    function updateViewMode() {
        $('viewModeLabel').textContent = tileMode ? 'List mode' : 'Tile mode';
        $('viewModeIcon').setAttribute('href', tileMode ? '#list' : '#tiles');
    }
    function render() {
        if (renaming) return;
        const query = $('filterInput').value.toLowerCase();
        const visible = entries.filter(entry => (showHidden || !entry.name.startsWith('.')) && entry.name.toLowerCase().includes(query));
        visible.sort((a, b) => Number(b.directory) - Number(a.directory) || (sort === 'name' ? a.name.localeCompare(b.name, undefined, {numeric: true}) : a[sort] - b[sort]) * (descending ? -1 : 1));
        visibleEntries = visible;
        const fragment = document.createDocumentFragment();
        for (const entry of visible) {
            const row = document.createElement(tileMode ? 'button' : 'tr');
            row.dataset.path = entry.path;
            row.title = entry.name + ' · ' + entry.mode;
            if (tileMode) { row.type = 'button'; row.className = 'file-tile'; }
            const label = document.createElement('div');
            label.className = 'file-name';
            label.innerHTML = entry.directory ? '<svg class="folder-icon"><use href="#folder"/></svg>' : '<svg class="file-icon"><use href="#file"/></svg>';
            const check = document.createElement('span'); check.className = 'selection-check'; check.setAttribute('aria-hidden', 'true'); check.innerHTML = '<svg><use href="#check"/></svg>'; label.prepend(check);
            const name = document.createElement('span');
            name.className = 'entry-name';
            name.textContent = entry.name;
            label.appendChild(name);
            if (entry.symlink) {
                const marker = document.createElement('span'); marker.className = 'link-marker'; marker.textContent = 'link'; label.appendChild(marker);
            }
            if (tileMode) {
                row.appendChild(label);
                const detail = document.createElement('span'); detail.className = 'tile-detail'; detail.textContent = entry.directory ? 'Folder' : size(entry.size); row.appendChild(detail);
            } else {
                const nameCell = document.createElement('td'); nameCell.appendChild(label); row.appendChild(nameCell);
                const sizeCell = document.createElement('td'); sizeCell.className = 'size'; sizeCell.textContent = entry.directory ? '—' : size(entry.size); row.appendChild(sizeCell);
                const modified = document.createElement('td'); modified.className = 'modified'; modified.textContent = new Date(entry.modified * 1000).toLocaleString(undefined, {year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false}); row.appendChild(modified);
            }
            bindEntry(row, entry);
            fragment.appendChild(row);
        }
        $('rows').replaceChildren();
        $('tileGrid').replaceChildren();
        $(tileMode ? 'tileGrid' : 'rows').appendChild(fragment);
        $('fileTable').hidden = tileMode;
        $('tileGrid').hidden = !tileMode;
        refreshSelection();
        updateViewMode();
        $('empty').hidden = visible.length > 0;
        $('empty').textContent = query ? 'No matching files' : 'No files in this folder';
        $('menuButton').classList.toggle('filtered', !!query);
    }
    async function list(path = directory, quiet = false) {
        const requestId = ++generation;
        if (!quiet) status('Loading…');
        const data = await api('list?' + new URLSearchParams({path}));
        if (requestId !== generation) return;
        const changed = directory !== data.path || !$('breadcrumbs').children.length;
        if (directory !== data.path) { selectedPaths.clear(); selected = null; selectionAnchor = null; }
        directory = data.path;
        entries = data.entries;
        if (!editingPath) $('pathInput').value = directory;
        if (changed) renderPath();
        const local = new URL(location.href); local.searchParams.set('path', directory); history.replaceState(null, '', local);
        if (reportedLocation !== directory) {
            FernandoPane.navigate({name: directory.split('/').pop() || '/', path: directory});
            reportedLocation = directory;
        }
        document.title = directory + ' · Files';
        render();
        if (!$('statusPopup').classList.contains('error')) $('statusPopup').hidden = true;
    }
    async function navigate(path) {
        if (!await leaveEditor()) return;
        clearTimeout(renameTimer); hideContext(); clearSearch(true);
        render();
        await list(path);
        closeViewer();
    }
    function closeViewer() {
        opened = null; dirty = false;
        $('viewerView').hidden = true; $('browserView').hidden = false;
        $('previewFrame').removeAttribute('src');
        $('dirtyMark').hidden = true;
    }
    function download(path) {
        const anchor = document.createElement('a'); anchor.href = url('content', path); anchor.download = path.split('/').pop(); document.body.appendChild(anchor); anchor.click(); anchor.remove();
    }
    async function openEntry(entry) {
        clearTimeout(renameTimer);
        if (entry.directory) return navigate(entry.path);
        if (!await leaveEditor()) return;
        clearSearch(true); render();
        const extension = entry.name.split('.').pop().toLowerCase();
        if (['png','jpg','jpeg','gif','webp','svg','bmp','ico','avif'].includes(extension)) {
            const image = document.createElement('img'); image.src = url('content', entry.path, true); image.alt = entry.name; $('messages').replaceChildren(image); image.click(); return;
        }
        const kind = extension === 'stl' || extension === 'pdf' ? extension : 'text';
        const data = kind === 'text' ? await api('text?' + new URLSearchParams({path: entry.path})) : null;
        opened = {path: entry.path, kind, revision: data?.revision}; dirty = false;
        $('viewerName').textContent = entry.name; $('dirtyMark').hidden = true;
        $('browserView').hidden = true; $('viewerView').hidden = false;
        $('saveButton').hidden = kind !== 'text'; $('editor').hidden = kind !== 'text'; $('previewFrame').hidden = kind === 'text'; $('externalViewer').hidden = kind !== 'pdf';
        if (kind === 'text') {
            loadingEditor = true;
            editor.session.setMode(ace.require('ace/ext/modelist').getModeForPath(entry.name).mode);
            editor.setValue(data.text, -1); loadingEditor = false; editor.resize();
        } else {
            const target = url(kind === 'stl' ? 'stl' : 'content', entry.path, kind === 'pdf');
            $('previewFrame').src = target;
            $('externalViewer').href = target;
        }
        $('statusPopup').hidden = true;
    }
    async function save() {
        if (opened?.kind !== 'text') return;
        const target = opened;
        const text = editor.getValue();
        const data = await api('save', {path: target.path, text, revision: target.revision});
        if (opened === target) {
            opened.revision = data.revision;
            dirty = editor.getValue() !== text; $('dirtyMark').hidden = !dirty;
            status('Saved · ' + target.path);
        }
    }
    async function operation(action) {
        const targets = entries.filter(entry => selectedPaths.has(entry.path));
        if (!targets.length) return;
        let destination;
        if (action === 'delete') {
            const description = targets.length === 1 ? targets[0].path : targets.length + ' selected items';
            if (!await prompt('Delete', 'Permanently delete ' + description + ' and any folder contents?', null, true)) return;
        } else {
            destination = await prompt(action === 'copy' ? 'Copy to' : 'Move to', targets.length > 1 ? 'Destination folder. Existing files will not be overwritten.' : 'Destination path. Existing files will not be overwritten.', targets.length > 1 ? directory : targets[0].path);
            if (!destination) return;
            if (!destination.startsWith('/')) destination = directory.replace(/\/$/, '') + '/' + destination;
        }
        for (const entry of targets) {
            const body = {action, path: entry.path};
            if (destination) body.destination = targets.length > 1 ? destination.replace(/\/$/, '') + '/' + entry.name : destination;
            if (body.destination === entry.path) continue;
            status('Working on ' + entry.name + '…');
            await api('operation', body);
        }
        await list();
    }
    async function create(action) {
        const name = await prompt(action === 'mkdir' ? 'New folder' : 'New file', 'Name', '');
        if (!name) return;
        if (name.includes('/') || name === '.' || name === '..') throw new Error('Enter a filename without slashes');
        await api('operation', {action, path: directory.replace(/\/$/, '') + '/' + name}); await list();
    }
    async function upload(files, target = directory) {
        for (const file of files) {
            status('Uploading ' + file.name + '…');
            const form = new FormData(); form.set('path', target); form.set('file', file); await api('upload', form);
        }
        await list(); $('uploadInput').value = '';
    }
    function clearDropTarget() {
        document.body.classList.remove('dragging');
        for (const row of $('fileList').querySelectorAll('.drop-target')) row.classList.remove('drop-target');
    }
    function dropDirectory(event) {
        const path = event.target.closest('[data-path]')?.dataset.path;
        const entry = entries.find(item => item.path === path);
        return entry?.directory ? entry.path : directory;
    }
    async function transfer(targets, target, action) {
        for (const entry of targets) {
            const name = entry.path.slice(entry.path.lastIndexOf('/') + 1);
            const destination = target.replace(/\/$/, '') + '/' + name;
            if (destination === entry.path) continue;
            status((action === 'copy' ? 'Copying ' : 'Moving ') + entry.name + '…');
            await api('operation', {action, path:entry.path, destination});
        }
        await list();
    }
    function updateMarquee() {
        if (!marquee) return;
        const list = $('fileList');
        const box = list.getBoundingClientRect();
        const endX = marquee.x - box.left + list.scrollLeft;
        const endY = marquee.y - box.top + list.scrollTop;
        const left = Math.min(marquee.startX, endX), top = Math.min(marquee.startY, endY);
        const width = Math.abs(endX-marquee.startX), height = Math.abs(endY-marquee.startY);
        if (width > 4 || height > 4) marquee.moved = true;
        Object.assign($('selectionRectangle').style, {left:left+'px', top:top+'px', width:width+'px', height:height+'px'});
        $('selectionRectangle').hidden = !marquee.moved;
        selectedPaths.clear();
        for (const path of marquee.base) selectedPaths.add(path);
        if (marquee.moved) for (const row of list.querySelectorAll('[data-path]')) {
            const rect = row.getBoundingClientRect();
            const x = rect.left-box.left+list.scrollLeft, y = rect.top-box.top+list.scrollTop;
            if (x < left+width && x+rect.width > left && y < top+height && y+rect.height > top) selectedPaths.add(row.dataset.path);
        }
        refreshSelection();
    }
    function scrollMarquee() {
        if (!marquee) return;
        const list = $('fileList'), box = list.getBoundingClientRect();
        if (marquee.y < box.top+24) list.scrollTop -= 12;
        else if (marquee.y > box.bottom-24) list.scrollTop += 12;
        updateMarquee();
        marqueeFrame = requestAnimationFrame(scrollMarquee);
    }
    $('pathForm').onsubmit = event => {
        event.preventDefault();
        const value = $('pathInput').value;
        const path = value === '~' ? home : value.startsWith('~/') ? home + value.slice(1) : value;
        run(async () => { await navigate(path); finishPathEdit(); });
    };
    $('pathInput').addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); finishPathEdit(); $('pathDisplay').focus({preventScroll: true}); }
    });
    $('pathInput').addEventListener('blur', () => { if (editingPath) finishPathEdit(); });
    $('pathDisplay').addEventListener('click', event => {
        if (suppressPathClick) { event.preventDefault(); suppressPathClick = false; return; }
        const link = event.target.closest('a');
        if (link) {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); run(() => navigate(link.dataset.path));
        } else editPath();
    });
    $('pathDisplay').addEventListener('keydown', event => {
        if (event.key === 'F2' || (event.key === 'Enter' && event.target === $('pathDisplay'))) { event.preventDefault(); editPath(); }
    });
    $('pathDisplay').addEventListener('pointerdown', event => {
        resetPathGesture(); suppressPathClick = false;
        if (event.button !== 0 || event.isPrimary === false) return;
        pathGesture = {id: event.pointerId, x: event.clientX, y: event.clientY, start: performance.now()};
        pathHoldTimer = setTimeout(() => $('pathDisplay').classList.add('hold-ready'), 500);
    });
    $('pathDisplay').addEventListener('pointermove', event => {
        if (pathGesture && Math.hypot(event.clientX - pathGesture.x, event.clientY - pathGesture.y) > 8) resetPathGesture();
    });
    $('pathDisplay').addEventListener('pointerup', event => {
        const held = pathGesture?.id === event.pointerId && performance.now() - pathGesture.start >= 500;
        resetPathGesture();
        if (held) { event.preventDefault(); suppressPathClick = true; editPath(); }
    });
    $('pathDisplay').addEventListener('pointercancel', resetPathGesture);
    $('pathDisplay').addEventListener('pointerleave', resetPathGesture);
    $('pathDisplay').addEventListener('contextmenu', event => event.preventDefault());
    $('homeButton').onclick = () => run(() => navigate(home));
    $('refreshButton').onclick = () => run(() => list());
    $('menuButton').onclick = () => setMenu(!document.body.classList.contains('menu-open'));
    $('menuBackdrop').onclick = () => { if (touchUI) setMenu(false); };
    $('searchButton').onclick = () => {
        const open = $('searchField').hidden;
        $('searchField').hidden = !open;
        $('searchButton').setAttribute('aria-expanded', String(open));
        if (open) $('filterInput').focus();
        else { clearSearch(); render(); }
    };
    $('hiddenButton').onclick = () => { showHidden = !showHidden; $('hiddenButton').setAttribute('aria-pressed', String(showHidden)); render(); };
    $('tileButton').onclick = () => { tileMode = !tileMode; render(); if (touchUI) setMenu(false); };
    $('clearSearch').onclick = () => { clearSearch(); render(); $('filterInput').focus(); };
    $('filterInput').oninput = render;
    $('filterInput').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); if (touchUI) setMenu(false); $('fileList').focus({preventScroll: true}); } });
    document.addEventListener('click', event => {
        if (touchUI && !event.target.closest('#browserMenu, #menuButton')) setMenu(false);
        if (!event.target.closest('#fileContextMenu')) hideContext();
        if (!event.target.closest('[data-path]')) clearTimeout(renameTimer);
    });
    document.addEventListener('keydown', event => {
        if (!$('fileContextMenu').hidden) {
            if (event.key === 'Escape') { hideContext(); $('fileList').focus({preventScroll:true}); }
            else if (['ArrowUp','ArrowDown','Home','End'].includes(event.key)) {
                event.preventDefault();
                const buttons = [...$('fileContextMenu').querySelectorAll('button:not(:disabled)')];
                const index = buttons.indexOf(document.activeElement);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
                buttons[next].focus({preventScroll:true});
            }
            return;
        }
        if (touchUI && event.key === 'Escape' && document.body.classList.contains('menu-open')) { setMenu(false); $('menuButton').focus({preventScroll: true}); return; }
        if (opened || editingPath || renaming || $('promptDialog').open || $('propertiesDialog').open || event.target.closest('input, textarea, [contenteditable="true"], header, dialog, #editor')) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
            event.preventDefault(); for (const entry of visibleEntries) selectedPaths.add(entry.path); refreshSelection(); return;
        }
        if (event.key === 'Escape') { multiMode = false; select(null); return; }
        if (event.key === 'F2' && selectedPaths.size === 1) { event.preventDefault(); beginRename(selected); return; }
        if (event.key === 'Delete' && selectedPaths.size) { event.preventDefault(); run(() => operation('delete')); return; }
        if (!touchUI && !event.ctrlKey && !event.metaKey && !event.altKey && /^[\p{L}\p{N}]$/u.test(event.key)) {
            const matches = visibleEntries.filter(entry => entry.name.toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()));
            if (!matches.length) return;
            event.preventDefault();
            const index = matches.findIndex(entry => entry.path === selected?.path);
            const next = matches[(index+1) % matches.length];
            select(next);
            [...$('fileList').querySelectorAll('[data-path]')].find(row => row.dataset.path === next.path).scrollIntoView({block:'nearest'});
        }
    });
    $('dismissStatus').onclick = () => { $('statusPopup').hidden = true; $('statusPopup').classList.remove('error'); };
    for (const button of document.querySelectorAll('[data-sort]')) button.onclick = () => { descending = sort === button.dataset.sort ? !descending : false; sort = button.dataset.sort; render(); };
    $('openButton').onclick = menuAction(() => selected && openEntry(selected));
    $('propertiesButton').onclick = menuAction(showProperties);
    $('propertiesDialog').addEventListener('close', () => { propertiesRequest++; $('fileList').focus({preventScroll:true}); });
    $('renameButton').onclick = () => { hideContext(); beginRename(selected); };
    $('moveButton').onclick = menuAction(() => operation('move'));
    $('copyButton').onclick = menuAction(() => operation('copy'));
    $('deleteButton').onclick = menuAction(() => operation('delete'));
    $('downloadButton').onclick = () => { hideContext(); for (const entry of entries) if (selectedPaths.has(entry.path) && !entry.directory) download(entry.path); };
    $('copyNameButton').onclick = menuAction(async () => {
        await navigator.clipboard.writeText(entries.filter(entry => selectedPaths.has(entry.path)).map(entry => entry.name).join('\n'));
        status('File name' + (selectedPaths.size > 1 ? 's' : '') + ' copied');
    });
    $('selectItemsButton').onclick = () => { multiMode = !multiMode; hideContext(); refreshSelection(); };
    $('selectAllButton').onclick = () => { for (const entry of visibleEntries) selectedPaths.add(entry.path); hideContext(); refreshSelection(); };
    $('clearSelectionButton').onclick = () => { hideContext(); select(null); };
    $('selectionModeButton').onclick = () => { multiMode = false; hideContext(); select(null); };
    $('viewerDownload').onclick = () => opened && download(opened.path);
    $('previewFrame').addEventListener('load', () => {
        const doc = $('previewFrame').contentDocument;
        if (doc) doc.addEventListener('pointerdown', FernandoPane.activate, {capture: true, passive: true});
    });
    $('folderButton').onclick = drawerAction(() => create('mkdir'));
    $('fileButton').onclick = drawerAction(() => create('create'));
    $('uploadButton').onclick = () => { if (touchUI) setMenu(false); $('uploadInput').click(); };
    $('uploadInput').onchange = () => run(() => upload([...$('uploadInput').files]));
    $('closeViewer').onclick = () => run(async () => { if (await leaveEditor()) { closeViewer(); await list(); } });
    $('saveButton').onclick = () => run(save);
    editor.commands.addCommand({name: 'saveFile', bindKey: {win:'Ctrl-S', mac:'Command-S'}, exec: () => run(save)});
    $('fileList').addEventListener('keydown', event => {
        const entry = entries.find(item => item.path === event.target.closest('[data-path]')?.dataset.path) || selected;
        if (event.key === 'Enter' && entry && selectedPaths.size < 2 && !renaming) { event.preventDefault(); run(() => openEntry(entry)); }
    });
    $('fileList').addEventListener('pointerdown', event => {
        if (event.pointerType === 'touch' || event.button !== 0 || event.target.closest('[data-path], th, button, input') || renaming) return;
        clearTimeout(renameTimer); hideContext(); lastEntryClick = null;
        const list = $('fileList'), box = list.getBoundingClientRect();
        marquee = {id:event.pointerId, startX:event.clientX-box.left+list.scrollLeft, startY:event.clientY-box.top+list.scrollTop, x:event.clientX, y:event.clientY, base:new Set(event.ctrlKey || event.metaKey || event.shiftKey ? selectedPaths : []), moved:false};
        list.setPointerCapture(event.pointerId);
        list.focus({preventScroll:true});
        updateMarquee();
        marqueeFrame = requestAnimationFrame(scrollMarquee);
    });
    $('fileList').addEventListener('contextmenu', event => {
        if (event.target.closest('[data-path], input')) return;
        event.preventDefault(); resetEntryHold(); showContext(null, event.clientX, event.clientY);
    });
    $('fileList').addEventListener('pointerdown', event => {
        if (event.pointerType !== 'touch' || event.target.closest('[data-path], th, button, input')) return;
        suppressEntryClick = false;
        resetEntryHold();
        if (event.isPrimary === false) return;
        entryHold = {id:event.pointerId, x:event.clientX, y:event.clientY};
        entryHoldTimer = setTimeout(() => { suppressEntryClick = true; showContext(null, event.clientX, event.clientY); entryHold = null; }, 500);
    });
    $('fileList').addEventListener('pointermove', event => { if (entryHold && Math.hypot(event.clientX-entryHold.x, event.clientY-entryHold.y) > 8) resetEntryHold(); });
    $('fileList').addEventListener('pointerup', resetEntryHold);
    $('fileList').addEventListener('pointercancel', resetEntryHold);
    $('fileList').addEventListener('click', event => {
        if (suppressEntryClick) { suppressEntryClick = false; event.preventDefault(); event.stopPropagation(); }
    });
    $('fileList').addEventListener('pointermove', event => {
        if (!marquee || marquee.id !== event.pointerId) return;
        marquee.x = event.clientX; marquee.y = event.clientY; updateMarquee();
    });
    const finishMarquee = event => {
        if (!marquee || marquee.id !== event.pointerId) return;
        marquee = null; cancelAnimationFrame(marqueeFrame); $('selectionRectangle').hidden = true;
        if ($('fileList').hasPointerCapture(event.pointerId)) $('fileList').releasePointerCapture(event.pointerId);
    };
    $('fileList').addEventListener('pointerup', finishMarquee);
    $('fileList').addEventListener('pointercancel', finishMarquee);
    $('fileList').addEventListener('lostpointercapture', finishMarquee);
    for (const name of ['dragenter', 'dragover']) $('fileList').addEventListener(name, event => {
        const types = [...event.dataTransfer.types];
        if (!types.includes('Files') && !FernandoFileDrag.accepts(event.dataTransfer)) return;
        event.preventDefault(); clearDropTarget();
        event.dataTransfer.dropEffect = types.includes('Files') || event.dataTransfer.effectAllowed === 'copy' || event.ctrlKey || event.altKey ? 'copy' : 'move';
        const target = dropDirectory(event);
        if (target === directory) document.body.classList.add('dragging');
        else event.target.closest('[data-path]').classList.add('drop-target');
    });
    $('fileList').addEventListener('dragleave', event => { if (!$('fileList').contains(event.relatedTarget)) clearDropTarget(); });
    $('fileList').addEventListener('drop', event => {
        event.preventDefault(); clearDropTarget();
        const target = dropDirectory(event);
        if (FernandoFileDrag.accepts(event.dataTransfer)) {
            let data;
            try {
                data = FernandoFileDrag.take(event.dataTransfer);
            } catch (error) {
                status(error.message, true);
                return;
            }
            run(() => transfer(data.entries, target, data.kind === 'references' || event.ctrlKey || event.altKey ? 'copy' : 'move'));
        } else if (event.dataTransfer.files.length) {
            if ([...event.dataTransfer.items].some(item => item.webkitGetAsEntry?.()?.isDirectory)) {
                status('Drop files to upload. Uploading folders is not supported.', true);
                return;
            }
            run(() => upload([...event.dataTransfer.files], target));
        }
    });
    $('viewerView').addEventListener('transitionend', event => { if (event.propertyName === 'margin-right') editor.resize(); });
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    $('hiddenButton').setAttribute('aria-pressed', String(showHidden));
    updateViewMode();
    run(() => list());
    setInterval(() => { if (!document.hidden && !opened && !editingPath && !pathGesture && !entryHold && !marquee && !renaming && $('fileContextMenu').hidden && !$('promptDialog').open && !$('propertiesDialog').open) run(() => list(directory, true)); }, 5000);
})();
