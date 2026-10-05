let isSplit = false;
let activeTerminal = 1;
let _lastDirectPaneTouch = 0;
let _lastDirectPaneTarget = 0;

const paneController = {
    panes: new Map([1, 2].map(id => [id, {
        id,
        surface: 'terminal',
        terminalSession: null,
        contentKey: null,
        jupyterPath: null,
    }])),
    sessionTypes: new Map(),
    focusDocuments: new WeakSet(),

    get(id) {
        return this.panes.get(id);
    },

    register(type, adapter) {
        if (this.sessionTypes.has(type)) throw new Error('Duplicate pane session type: ' + type);
        for (const method of ['matches', 'mount', 'context', 'sessions', 'sidebar']) {
            if (typeof adapter[method] !== 'function') throw new Error(type + ' requires ' + method);
        }
        this.sessionTypes.set(type, { type, surface: 'browser', mobileClass: 'chat-active', ownsKey: adapter.matches, dismiss: { emptyBrowser: true, fit: true }, syncOnClose: true, ...adapter });
    },

    adapter(key) {
        if (typeof key !== 'string' || !key) return null;
        return [...this.sessionTypes.values()].sort((a, b) => (b.priority || 0) - (a.priority || 0)).find(adapter => adapter.matches(key)) || null;
    },

    open(key, options = {}) {
        const adapter = this.adapter(key);
        if (!adapter) return false;
        const prepared = adapter.prepare ? adapter.prepare(key, options) : { key };
        if (prepared === false) return true;
        const pane = activeTerminal;
        const context = { ...prepared, pane, state: this.get(pane), elements: this.elements(pane), options };
        if (adapter.toggle && this.isEmbedded(pane, adapter.type)) {
            this.showTerminal(pane, adapter.toggle);
        } else {
            if (adapter.surface === 'browser') context.browser = this.showBrowser(pane, context.key);
            else if (context.state.surface === 'browser') this.showTerminal(pane, { clearLocation: true });
            if (adapter.beforeMount) adapter.beforeMount(context);
            const mounted = adapter.mount(context);
            if (mounted) Object.assign(context, mounted);
        }
        this.finishOpen(adapter.type, context);
        if (adapter.afterOpen) adapter.afterOpen(context);
        return true;
    },

    dismiss(key, options = {}) {
        for (const pane of this.panes.keys()) {
            const state = this.get(pane);
            const matches = options.matches ? options.matches(pane) : state.contentKey === key;
            if (matches) this.showTerminal(pane, options);
        }
    },

    close(key) {
        const adapter = this.adapter(key);
        if (!adapter?.close) return;
        if (adapter.closeBeforeDismiss) adapter.close(key);
        if (adapter.dismiss) this.dismiss(key, typeof adapter.dismiss === 'function' ? adapter.dismiss(key) : adapter.dismiss);
        if (!adapter.closeBeforeDismiss) adapter.close(key);
        if (adapter.syncOnClose) syncUrlParams();
        if (adapter.dismiss) updateKbdBtn();
    },

    inventory(data) {
        return [...this.sessionTypes.values()].sort((a, b) => (a.sidebarOrder || 0) - (b.sidebarOrder || 0)).flatMap(adapter => adapter.sessions(data).map(session => ({ ...session, adapter })));
    },

    sidebarItem(session) {
        const { adapter, key } = session;
        const view = adapter.sidebar(session);
        const item = document.createElement('div');
        item.className = 'session-item' + (view.className ? ' ' + view.className : '');
        if (view.title) item.title = view.title;
        item.dataset.session = key;
        Object.assign(item.dataset, view.dataset || {});
        const label = document.createElement('span');
        label.className = 'session-name' + (view.labelClass ? ' ' + view.labelClass : '');
        if (view.icon) label.innerHTML = view.icon;
        let title;
        if (view.label) {
            const result = view.label();
            label.appendChild(result.element);
            title = result.title;
        } else {
            title = document.createTextNode(session.name);
            label.appendChild(title);
        }
        item.appendChild(label);
        const bindAction = (element, run) => {
            element.addEventListener('click', event => { event.stopPropagation(); run(); });
            element.addEventListener('touchstart', event => event.stopPropagation());
            element.addEventListener('touchend', event => { event.stopPropagation(); event.preventDefault(); run(); });
        };
        if (view.collapse) {
            const chevron = document.createElement('span');
            chevron.className = 'parent-chevron' + (view.collapse.isCollapsed ? '' : ' expanded');
            chevron.innerHTML = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="3 2 7 5 3 8"/></svg>';
            bindAction(chevron, view.collapse.toggle);
            item.appendChild(chevron);
        }
        const close = adapter.close ? () => this.close(key) : null;
        if (close || view.button) {
            const button = document.createElement('button');
            button.className = 'close-btn';
            button.innerHTML = view.button?.icon || '<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
            if (view.closeLabel) {
                button.title = view.closeLabel;
                button.setAttribute('aria-label', view.closeLabel);
            }
            bindAction(button, view.button ? view.button.run : close);
            item.appendChild(button);
        }
        const rename = view.rename ? () => {
            const oldName = title.textContent;
            const input = document.createElement('input');
            input.className = 'session-rename-input';
            input.value = oldName;
            label.replaceWith(input);
            input.focus();
            input.select();
            const commit = () => {
                if (!input.parentNode) return;
                const name = input.value.trim();
                input.replaceWith(label);
                if (name && name !== oldName) {
                    if (view.optimisticRename) title.textContent = name;
                    view.rename(name);
                }
            };
            input.addEventListener('keydown', event => {
                event.stopPropagation();
                if (event.key === 'Enter') { event.preventDefault(); commit(); }
                if (event.key === 'Escape') input.replaceWith(label);
            });
            input.addEventListener('blur', commit);
            input.addEventListener('click', event => event.stopPropagation());
        } : null;
        item.addEventListener('click', event => this.select(key, event, item, rename));
        if (!session.pinned) {
            this.bindContextMenu(item, (event, x, y) => showSessionContextMenu(key, x, y, rename, close, view.sleep, view.clone, view.collapse));
            makeSessionDraggable(item, key);
        }
        return item;
    },

    select(key, event, item, rename) {
        const adapter = this.adapter(key);
        if (adapter.selectInactiveOnly && item.classList.contains('active')) {
            if (event.detail === 2 && rename) rename();
            return;
        }
        if (!adapter.skipSelectionHighlight) highlightSidebarItem(key);
        const existing = adapter.activateExisting && [...this.panes.keys()].find(pane => this.get(pane).contentKey === key);
        if (existing) setActiveTerminal(existing, true);
        else this.open(adapter.selectionKey ? adapter.selectionKey(key) : key);
        if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
    },

    restore(sessions = null) {
        if (window._urlParamsProcessed || this.get(1).terminalSession || this.get(1).surface === 'browser') return false;
        const params = new URLSearchParams(window.location.search);
        const first = params.get('session');
        const second = params.get('session2');
        const available = key => {
            const adapter = this.adapter(key);
            return adapter && (!sessions || !adapter.requiresAvailable || sessions.includes(key));
        };
        if (available(first)) this.open(first);
        else if (sessions?.length) {
            const saved = sessionStorage.getItem('fernando_session1');
            this.open(saved && sessions.includes(saved) ? saved : sessions[0]);
        } else if (!sessions && !first) openNewSessionModal();
        if (params.get('split') === '1') {
            if (!isSplit) toggleSplit();
            if (available(second)) this.open(second);
            const active = parseInt(params.get('active'));
            if (active === 1) setActiveTerminal(1);
            if (sessions) {
                const key = active === 1 ? first : second;
                if (key) highlightSidebarItem(key);
            } else refreshSidebarHighlights();
        }
        window._urlParamsProcessed = true;
        return true;
    },

    elements(id) {
        return {
            terminal: document.getElementById(`terminal${id}`),
            browser: document.getElementById(`browser${id}`),
            container: document.getElementById(`terminal${id}-container`),
        };
    },

    showBrowser(id, contentKey) {
        const state = this.get(id);
        const elements = this.elements(id);
        if (state.contentKey !== contentKey) this.release(id);
        state.surface = 'browser';
        state.contentKey = contentKey;
        elements.terminal.classList.add('hidden');
        elements.browser.classList.remove('hidden');
        state.terminalSession = null;
        return elements.browser;
    },

    release(id) {
        const key = this.get(id).contentKey;
        const adapter = this.adapter(key);
        if (adapter?.unmount) adapter.unmount({ pane: id, key, elements: this.elements(id) });
    },

    ready(key) {
        const adapter = this.adapter(key);
        if (!adapter?.ready) throw new Error('Session has no ready lifecycle: ' + key);
        for (const pane of this.panes.keys()) {
            if (this.get(pane).surface !== 'browser' || this.get(pane).contentKey !== key) continue;
            adapter.ready({ pane, key, browser: this.elements(pane).browser });
            if (adapter.firstReadyOnly) break;
        }
        refreshSidebarHighlights();
        syncUrlParams();
    },

    showTerminal(id, { clearContent = true, clearLocation = false, emptyBrowser = false, fit = false } = {}) {
        const state = this.get(id);
        const elements = this.elements(id);
        if (emptyBrowser) this.release(id);
        state.surface = 'terminal';
        if (clearContent) state.contentKey = null;
        if (clearLocation) state.jupyterPath = null;
        elements.terminal.classList.remove('hidden');
        elements.browser.classList.add('hidden');
        if (emptyBrowser) elements.browser.innerHTML = '';
        if (fit) setTimeout(doFit, 100);
    },

    finishOpen(type, context) {
        const adapter = this.sessionTypes.get(type);
        if (adapter.surface === 'terminal') {
            highlightSidebarItem(context.key);
            updatePaneBorders();
            if (context.entry.ready) context.entry.wterm.focus();
            setTimeout(doFit, 100);
            syncUrlParams();
            return;
        }
        refreshSidebarHighlights();
        updatePaneBorders();
        if (this.sessionTypes.get(type).controlsBeforeUrl) {
            updateKbdBtn();
            syncUrlParams();
        } else {
            syncUrlParams();
            updateKbdBtn();
        }
    },

    browserKey(id) {
        const key = this.get(id).contentKey;
        if (key) {
            for (const adapter of this.sessionTypes.values()) {
                if (adapter.ownsKey?.(key)) return key;
            }
            return 'notebook:' + key;
        }
        const iframe = this.elements(id).browser.querySelector('iframe');
        if (iframe && iframe.src) {
            for (const adapter of this.sessionTypes.values()) {
                const detected = adapter.keyFromUrl?.(iframe.src);
                if (detected) return detected;
            }
        }
        return 'desktop';
    },

    sessionKey(id) {
        const state = this.get(id);
        return state.surface === 'browser' ? this.browserKey(id) : state.terminalSession;
    },

    locationKey(id) {
        const key = this.sessionKey(id);
        const adapter = this.adapter(key);
        return adapter?.serialize ? adapter.serialize(key, this.get(id)) : key;
    },

    isEmbedded(id, type) {
        if (this.get(id).surface !== 'browser') return false;
        const iframe = this.elements(id).browser.querySelector('iframe');
        const adapter = this.sessionTypes.get(type);
        return adapter.urlFragment ? !!(iframe && iframe.src.includes(adapter.urlFragment)) : adapter.matches(this.get(id).contentKey || '');
    },

    activeAdapter() {
        const state = this.get(activeTerminal);
        if (state.surface === 'terminal') return this.sessionTypes.get('terminal');
        return [...this.sessionTypes.values()].find(adapter => adapter.surface === 'browser' && this.isEmbedded(activeTerminal, adapter.type));
    },

    context(id) {
        const state = this.get(id);
        if (state.surface === 'terminal') return this.sessionTypes.get('terminal').context(state.terminalSession);
        if (state.contentKey) {
            for (const adapter of this.sessionTypes.values()) {
                if (adapter.ownsKey?.(state.contentKey)) return adapter.context(state.contentKey, state);
            }
            return { type: 'notebook', notebook: state.contentKey };
        }
        for (const adapter of this.sessionTypes.values()) {
            if (adapter.contextWithoutKey && this.isEmbedded(id, adapter.type)) return adapter.context(null);
        }
        return { type: 'browser' };
    },

    activate(termNum, direct) {
        if (direct) {
            _lastDirectPaneTouch = Date.now();
            _lastDirectPaneTarget = termNum;
        } else {
            if (Date.now() - _lastDirectPaneTouch < 2000 && _lastDirectPaneTarget !== termNum) return;
        }
        activeTerminal = termNum;
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
        broadcastPaneActive();
    },

    toggleSplit() {
        isSplit = !isSplit;
        if (isSplit) {
            document.getElementById('terminal1-container').classList.remove('hidden');
            document.getElementById('terminal2-container').classList.remove('hidden');
            setActiveTerminal(2, true);
        } else {
            const keep = activeTerminal;
            const discard = keep === 1 ? 2 : 1;
            document.getElementById(`terminal${discard}-container`).classList.add('hidden');
            emitWithCsrf('detach_viewer', { terminal: discard });
            _paneSession[discard] = null;
            setActiveTerminal(keep, true);
        }
        setTimeout(doFit, 100);
        syncUrlParams();
        broadcastGroupColors();
        refreshSidebarHighlights();
    },

    iframePane(source) {
        for (const id of this.panes.keys()) {
            const iframe = this.elements(id).browser.querySelector('iframe');
            if (iframe && iframe.contentWindow === source) return id;
        }
        return null;
    },

    post(pane, event, payload) {
        const frame = this.elements(pane).browser.querySelector('iframe');
        if (!frame?.contentWindow) return;
        frame.contentWindow.postMessage({ type: 'fernando-pane', version: 1, event, payload }, new URL(frame.src).origin);
    },

    fullContext(requestingPane) {
        const context = { split: isSplit };
        for (const pane of this.panes.keys()) context['pane' + pane] = this.context(pane);
        const key = this.get(requestingPane).contentKey;
        const groupId = key && _cachedSessionGroups[key];
        const group = groupId && _cachedGroups.find(group => group.id === groupId);
        if (group) {
            const sessions = this.inventory({ ..._cachedData, sessions: _cachedSessions, chat_sessions: _cachedChatSessions });
            context.group = { id: group.id, name: group.name, color: group.color, sessions: [] };
            const byKey = new Map(sessions.map(session => [session.key, session]));
            for (const [key, id] of Object.entries(_cachedSessionGroups)) {
                const session = byKey.get(key);
                if (id === groupId && session) context.group.sessions.push(session.adapter.describeSession ? session.adapter.describeSession(session) : key);
            }
        }
        return context;
    },

    broadcastState() {
        for (const pane of this.panes.keys()) {
            const key = this.get(pane).contentKey;
            const groupId = key && _cachedSessionGroups[key];
            const group = groupId && _cachedGroups.find(group => group.id === groupId);
            this.post(pane, 'state', { active: pane === activeTerminal, split: isSplit, group: group || null });
        }
    },

    receive(event) {
        const pane = this.iframePane(event.source);
        if (!pane) return;
        const frame = this.elements(pane).browser.querySelector('iframe');
        if (event.source !== frame.contentWindow || event.origin !== new URL(frame.src).origin) return;
        const message = event.data;
        if (!message || message.type !== 'fernando-pane' || message.version !== 1 || typeof message.event !== 'string') return;
        const adapter = this.adapter(this.sessionKey(pane));
        if (message.event === 'ready' || message.event === 'context-request') {
            this.post(pane, 'context', this.fullContext(pane));
            if (message.event === 'ready') this.broadcastState();
        } else if (message.event === 'activate') {
            if (this.get(pane).surface !== 'browser') return;
            if (pane === 1 || isSplit || !adapter?.dismissSidebarOnActivate) setActiveTerminal(pane, true);
            if (adapter?.dismissSidebarOnActivate && window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
        } else if (message.event === 'navigate' && adapter?.navigate) {
            const location = message.payload;
            if (!location || typeof location.name !== 'string' || !location.name || typeof location.path !== 'string') return;
            const update = adapter.navigate(pane, location);
            if (update && typeof update.location === 'string') {
                this.get(pane).location = update.location;
                syncUrlParams();
            }
        }
    },

    installBridge() {
        window.addEventListener('message', this.receive.bind(this));
    },

    isIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
            (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    },

    viewForElement(element) {
        let node = element;
        while (node && node.isConnected) {
            const root = node.getRootNode();
            if (root === document) {
                for (const pane of this.panes.keys()) {
                    const elements = this.elements(pane);
                    for (const surface of ['terminal', 'browser']) {
                        if (elements[surface].contains(node)) return { pane, surface };
                    }
                }
                return null;
            }
            if (root.host) {
                node = root.host;
            } else {
                const frame = root.defaultView?.frameElement;
                if (!frame || frame.contentDocument !== root) return null;
                node = frame;
            }
        }
        return null;
    },

    activeInput(doc) {
        let element = doc.activeElement;
        while (element?.shadowRoot?.activeElement) element = element.shadowRoot.activeElement;
        if (!element || element.disabled || element.readOnly) return null;
        if (element.tagName === 'TEXTAREA' || element.isContentEditable) return element;
        if (element.tagName === 'INPUT' && ['text', 'search', 'url', 'tel', 'email', 'password', 'number'].includes(element.type)) return element;
        return null;
    },

    preserveInputFocus(input) {
        if (!this.isIOS() || !isSplit) return;
        const view = this.viewForElement(input);
        if (!view || view.pane !== 1 || this.get(view.pane).surface !== view.surface) return;
        if (this.activeInput(input.ownerDocument) !== input) return;
        input.focus({ preventScroll: true });
    },

    bindFocusFrame(frame) {
        if (!this.viewForElement(frame)) return;
        const doc = frame.contentDocument;
        if (doc) this.bindFocusDocument(doc);
    },

    bindFocusDocument(doc) {
        if (this.focusDocuments.has(doc)) return;
        this.focusDocuments.add(doc);
        let tap = null;
        doc.addEventListener('touchstart', event => {
            tap = null;
            if (event.touches.length !== 1) return;
            const view = this.viewForElement(event.composedPath()[0]);
            if (!view || this.get(view.pane).surface !== view.surface) return;
            const adapter = this.adapter(this.sessionKey(view.pane));
            if (!adapter?.focusOnTap) return;
            const touch = event.touches[0];
            tap = {view, adapter, key: this.sessionKey(view.pane), x: touch.clientX, y: touch.clientY, at: event.timeStamp};
        }, {capture: true, passive: true});
        doc.addEventListener('touchmove', event => {
            if (!tap) return;
            const touch = event.touches[0];
            if (event.touches.length !== 1 || Math.hypot(touch.clientX - tap.x, touch.clientY - tap.y) > 8) tap = null;
        }, {capture: true, passive: true});
        doc.addEventListener('touchcancel', () => { tap = null; }, {capture: true, passive: true});
        doc.addEventListener('touchend', event => {
            const completed = tap;
            tap = null;
            if (!completed || event.touches.length || event.timeStamp - completed.at >= 500) return;
            const view = this.viewForElement(event.composedPath()[0]);
            if (!view || view.pane !== completed.view.pane || view.surface !== completed.view.surface) return;
            if (this.get(view.pane).surface !== view.surface || this.sessionKey(view.pane) !== completed.key) return;
            if (doc.getSelection()?.isCollapsed === false) return;
            completed.adapter.focusOnTap(view.pane);
        }, {capture: true, passive: true});
        let gesture = false;
        let focusedDuringGesture = null;
        doc.addEventListener('pointerdown', () => {
            gesture = true;
            focusedDuringGesture = null;
        }, {capture: true, passive: true});
        doc.addEventListener('pointercancel', () => {
            gesture = false;
            focusedDuringGesture = null;
        }, {capture: true, passive: true});
        doc.addEventListener('focusin', () => {
            if (gesture) focusedDuringGesture = this.activeInput(doc);
        }, true);
        doc.addEventListener('click', event => {
            const input = this.activeInput(doc);
            const target = event.composedPath()[0];
            const newlyFocused = gesture && focusedDuringGesture === input;
            gesture = false;
            focusedDuringGesture = null;
            if (input && (input.contains(target) || newlyFocused)) this.preserveInputFocus(input);
        });
        doc.addEventListener('load', event => {
            if (event.target.tagName === 'IFRAME') this.bindFocusFrame(event.target);
        }, true);
        for (const frame of doc.querySelectorAll('iframe')) this.bindFocusFrame(frame);
    },

    installFocusPolicy() {
        if (this.isIOS()) this.bindFocusDocument(document);
    },

    bindContextMenu(item, showMenu) {
        item.addEventListener('contextmenu', function(e) {
            e.preventDefault();
            showMenu(e, e.clientX, e.clientY);
        });
        let holdTimer = null;
        let touchMoved = false;
        item.addEventListener('touchstart', function(e) {
            touchMoved = false;
            holdTimer = setTimeout(() => {
                if (!touchMoved) {
                    holdTimer = 'fired';
                    showMenu(e, e.touches[0].clientX, e.touches[0].clientY);
                }
            }, 500);
        }, {passive: true});
        item.addEventListener('touchend', function(e) {
            if (holdTimer === 'fired') e.preventDefault();
            else clearTimeout(holdTimer);
            holdTimer = null;
        });
        item.addEventListener('touchmove', function() {
            touchMoved = true;
            if (holdTimer === 'fired') dismissContextMenus();
            else clearTimeout(holdTimer);
        });
    },
};

function setActiveTerminal(termNum, direct) {
    paneController.activate(termNum, direct);
    updatePaneBorders();
    refreshSidebarHighlights();
}

function toggleSplit() {
    paneController.toggleSplit();
}
