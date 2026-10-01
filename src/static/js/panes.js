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
        this.sessionTypes.set(type, adapter);
    },

    open(key) {
        for (const adapter of this.sessionTypes.values()) {
            if (adapter.matches(key)) {
                adapter.open(key);
                return true;
            }
        }
        return false;
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
        state.surface = 'browser';
        state.contentKey = contentKey;
        elements.terminal.classList.add('hidden');
        elements.browser.classList.remove('hidden');
        state.terminalSession = null;
        return elements.browser;
    },

    showTerminal(id, { clearContent = true, clearLocation = false, emptyBrowser = false, fit = false } = {}) {
        const state = this.get(id);
        const elements = this.elements(id);
        state.surface = 'terminal';
        if (clearContent) state.contentKey = null;
        if (clearLocation) state.jupyterPath = null;
        elements.terminal.classList.remove('hidden');
        elements.browser.classList.add('hidden');
        if (emptyBrowser) elements.browser.innerHTML = '';
        if (fit) setTimeout(doFit, 100);
    },

    finishOpen(type) {
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
                if (adapter.ownsKey(key)) return key;
            }
            return 'notebook:' + key;
        }
        const iframe = this.elements(id).browser.querySelector('iframe');
        if (iframe && iframe.src) {
            for (const adapter of this.sessionTypes.values()) {
                const detected = adapter.keyFromUrl(iframe.src);
                if (detected) return detected;
            }
        }
        return 'desktop';
    },

    sessionKey(id) {
        const state = this.get(id);
        return state.surface === 'browser' ? this.browserKey(id) : state.terminalSession;
    },

    isEmbedded(id, type) {
        if (this.get(id).surface !== 'browser') return false;
        const iframe = this.elements(id).browser.querySelector('iframe');
        return !!(iframe && iframe.src.includes(this.sessionTypes.get(type).urlFragment));
    },

    context(id) {
        const state = this.get(id);
        if (state.surface === 'terminal') return { type: 'terminal', session: state.terminalSession };
        if (state.contentKey) {
            for (const adapter of this.sessionTypes.values()) {
                if (adapter.ownsKey(state.contentKey)) return adapter.context(state.contentKey);
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

paneController.register('chat', {
    type: 'chat',
    matches: key => key.startsWith('chat:'),
    ownsKey: key => key.startsWith('chat:'),
    open: key => openChatPane(key.slice(5)),
    urlFragment: '/chat/',
    context: () => ({ type: 'chat' }),
    contextWithoutKey: true,
    keyFromUrl: url => {
        const match = url.match(/\/chat\/([^/?#]+)/);
        return match ? 'chat:' + match[1] : null;
    },
    controlsBeforeUrl: true,
});

paneController.register('notebook', {
    type: 'notebook',
    matches: key => key.startsWith('notebook:'),
    ownsKey: key => key.startsWith('notebook:'),
    open: key => openNotebook(key.slice(9)),
    urlFragment: '/notes/',
    context: key => ({ type: 'notebook', notebook: key.slice(9) }),
    keyFromUrl: url => {
        const match = url.match(/\/notes\/([^/?#]+)\//);
        return match ? 'notebook:' + match[1] : null;
    },
});

paneController.register('jupyter', {
    type: 'jupyter',
    matches: key => key === 'jupyter' || key.startsWith('jupyter:'),
    ownsKey: key => key.startsWith('jupyter:'),
    open: key => openJupyter(key.startsWith('jupyter:') ? key.slice(8) : undefined),
    urlFragment: '/jupyter/',
    context: key => ({ type: 'jupyter', notebook: key.slice(8) }),
    keyFromUrl: url => url.includes('/jupyter/') ? 'jupyter:Jupyter' : null,
});

paneController.register('desktop', {
    type: 'desktop',
    matches: key => key === 'desktop',
    ownsKey: () => false,
    open: () => toggleDesktop(),
    urlFragment: '/kasm/',
    context: () => ({ type: 'desktop' }),
    contextWithoutKey: true,
    keyFromUrl: url => url.includes('/kasm/') ? 'desktop' : null,
});
