// --- Terminal Setup (wterm) ---
// Each session gets its own WTerm instance. Panes show/hide them on switch.

// session_name -> { wterm, element, pane, ready, pending }
const termInstances = {};

// pane -> session_name (for routing output from server)
const _paneSession = { 1: null, 2: null };

function measureTermSize(container) {
    const style = getComputedStyle(container);
    const row = document.createElement('div');
    row.className = 'term-row';
    row.style.cssText = 'position:absolute;visibility:hidden;';
    const probe = document.createElement('span');
    probe.style.cssText = 'white-space:pre;width:auto;';
    probe.textContent = 'W';
    row.appendChild(probe);
    container.appendChild(row);
    const rect = probe.getBoundingClientRect();
    const rowHeight = row.getBoundingClientRect().height;
    row.remove();
    if (!rect.width || !rowHeight) return null;
    const pad = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    const padV = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
    const w = container.clientWidth - pad;
    const h = container.clientHeight - padV - (container.querySelector('.ios-spacer')?.getBoundingClientRect().height || 0);
    return { cols: Math.max(1, Math.floor(w / rect.width)), rows: Math.max(1, Math.floor(h / rowHeight)) };
}

function fitTerminal(entry) {
    if (!entry.ready || entry.element.style.visibility === 'hidden') return;
    const size = measureTermSize(entry.element);
    if (size && (size.cols !== entry.wterm.cols || size.rows !== entry.wterm.rows)) {
        entry.wterm.resize(size.cols, size.rows);
    }
}

// Get or create a WTerm for a session, placed in the given pane.
function getOrCreateTerm(sessionName, pane) {
    let entry = termInstances[sessionName];
    if (entry) {
        // Move to different pane if needed
        if (entry.pane !== pane) {
            document.getElementById('terminal' + pane).appendChild(entry.element);
            entry.pane = pane;
        }
        return entry;
    }

    const container = document.getElementById('terminal' + pane);

    // Create element with same classes as the original .terminal div
    const el = document.createElement('div');
    el.className = 'terminal';
    container.appendChild(el);

    const wterm = new WTerm(el, {
        autoResize: false,
        cursorBlink: true,
        onResize: (cols, rows) => { emitWithCsrf('resize', { terminal: entry.pane, rows, cols }); },
        onInputScroll: () => {
            if (!entry?.positionInputLine) return false;
            entry.followInput = entry.positionInputLine();
            return entry.followInput;
        },
        onRender: () => {
            if (entry?.followInput) entry.positionInputLine();
        },
    });
    wterm.onData = data => { emitWithCsrf('input', { terminal: entry.pane, data }); };

    entry = { wterm, element: el, pane, ready: false, pending: [], firstAttach: true };
    termInstances[sessionName] = entry;

    wterm.init().then(() => {
        entry.ready = true;
        fitTerminal(entry);
        entry.resizeObserver = new ResizeObserver(() => fitTerminal(entry));
        entry.resizeObserver.observe(el);
        if (entry.pending.length) {
            entry.pending.forEach(d => wterm.write(d));
            entry.pending = [];
        }
        requestAnimationFrame(() => wterm.scrollToBottom());
        setupFocusScroll(entry);
    }).catch(err => console.error('wterm init failed for', sessionName, err));

    return entry;
}

// Show a session's terminal in a pane, hide all others in that pane.
function showTermInPane(sessionName, pane) {
    const container = document.getElementById('terminal' + pane);
    for (const [name, inst] of Object.entries(termInstances)) {
        if (inst.pane === pane && name !== sessionName) {
            inst.resizeObserver?.disconnect();
            inst.wterm.setRenderingPaused(true);
            inst.element.style.cssText = 'position:fixed;left:-9999px;visibility:hidden;';
        }
    }
    const entry = getOrCreateTerm(sessionName, pane);
    entry.element.style.cssText = '';
    entry.resizeObserver?.observe(entry.element);
    entry.wterm.setRenderingPaused(false);
    if (entry.ready) {
        fitTerminal(entry);
        requestAnimationFrame(() => entry.wterm.scrollToBottom());
    }
    // iOS paint bug: re-toggle spacer to force relayout after showing
    if (/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
        const spacer = entry.element.querySelector('.ios-spacer');
        if (spacer) {
            spacer.style.display = 'none';
            setTimeout(() => { spacer.style.display = ''; }, 0);
        }
    }
    return entry;
}

// Destroy a session's terminal
function destroyTerm(sessionName) {
    const entry = termInstances[sessionName];
    if (!entry) return;
    entry.resizeObserver?.disconnect();
    entry.focusController?.abort();
    clearTimeout(entry.focusTimer);
    entry.wterm.destroy();
    entry.element.remove();
    delete termInstances[sessionName];
}

// Get the active WTerm for a pane
function getTermForPane(pane) {
    const session = paneController.get(pane).terminalSession;
    return session && termInstances[session] ? termInstances[session].wterm : null;
}

// Compatibility shims — term1/term2 as dynamic getters
const _dummyTerm = { clear() {}, scrollToBottom() {}, focus() {}, rows: 24, cols: 80 };
Object.defineProperty(window, 'term1', { get: () => getTermForPane(1) || _dummyTerm });
Object.defineProperty(window, 'term2', { get: () => getTermForPane(2) || _dummyTerm });

// WTerm prototype shims
WTerm.prototype.clear = function() {};  // No-op — we hide/show, never clear
WTerm.prototype.scrollToBottom = function() {
    this.element.scrollTop = this.element.scrollHeight;
};

function doFit() {
    if (document.hidden) return;
    for (const entry of Object.values(termInstances)) fitTerminal(entry);
    if (paneController.get(1).surface === 'terminal') {
        const t = getTermForPane(1);
        if (t) emitWithCsrf('resize', { terminal: 1, rows: t.rows, cols: t.cols });
    }
    if (paneController.get(2).surface === 'terminal' && !document.getElementById('terminal2-container').classList.contains('hidden')) {
        const t = getTermForPane(2);
        if (t) emitWithCsrf('resize', { terminal: 2, rows: t.rows, cols: t.cols });
    }
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
        setTimeout(doFit, 100);
        // iOS: re-toggle spacers after returning from background/lock
        if (_isIOS) {
            setTimeout(() => {
                document.querySelectorAll('.ios-spacer').forEach(s => {
                    s.style.display = 'none';
                    setTimeout(() => { s.style.display = ''; }, 100);
                });
            }, 500);
        }
    }
});

// --- OSC 52 clipboard ---
let oscBuffer = '';
let oscBuffering = false;

function processOsc52(str) {
    if (!oscBuffering) {
        const start = str.indexOf('\x1b]52;');
        if (start === -1) return str;
        oscBuffering = true;
        oscBuffer = str.substring(start);
        str = str.substring(0, start);
    } else {
        oscBuffer += str;
        str = '';
    }
    const endBel = oscBuffer.indexOf('\x07');
    const endSt = oscBuffer.indexOf('\x1b\\');
    let end = -1, endLen = 0;
    if (endBel !== -1 && (endSt === -1 || endBel < endSt)) { end = endBel; endLen = 1; }
    else if (endSt !== -1) { end = endSt; endLen = 2; }
    if (end !== -1) {
        const seq = oscBuffer.substring(0, end);
        const remainder = oscBuffer.substring(end + endLen);
        oscBuffering = false;
        oscBuffer = '';
        const m = seq.match(/\x1b\]52;[^;]*;([A-Za-z0-9+/=]+)/);
        if (m) {
            try { navigator.clipboard.writeText(atob(m[1])).catch(() => {}); } catch(e) {}
        }
        return str + remainder;
    }
    return str;
}

// --- Output ---
socket.on('output', data => {
    let output = data.data;
    if (typeof output === 'string') output = processOsc52(output);
    if (!output) return;

    // Route to the session currently attached to this pane
    const sessionName = _paneSession[data.terminal];
    if (!sessionName) return;
    const entry = termInstances[sessionName];
    if (!entry) return;

    if (!entry.ready) {
        entry.pending.push(output);
        return;
    }
    entry.wterm.write(output);
    // iOS: re-toggle spacer only on screen clear to fix paint after grid rebuild
    if (_isIOS && typeof output === 'string' && output.includes('\x1b[2J')) _iosRetoggle(entry.element);
});

const _isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
let _iosRetoggleTimer = null;
function _iosRetoggle(el) {
    if (_iosRetoggleTimer) return;
    _iosRetoggleTimer = setTimeout(() => {
        _iosRetoggleTimer = null;
        const spacer = el.querySelector('.ios-spacer');
        if (spacer) {
            spacer.style.display = 'none';
            requestAnimationFrame(() => { spacer.style.display = ''; });
        }
    }, 50);
}

// --- Mobile: focus scroll ---
function setupMobileFocusScroll() {}

function setupFocusScroll(entry) {
    const textarea = entry.element.querySelector('textarea');
    if (!textarea) return;
    const controller = new AbortController();
    entry.focusController = controller;
    const signal = controller.signal;
    const fitVisibleViewport = () => {
        const viewport = window.visualViewport;
        const active = document.activeElement === textarea && !(typeof isSplit !== 'undefined' && isSplit);
        const keyboard = active && viewport && viewport.scale === 1 && viewport.height < window.innerHeight - 100;
        const parent = entry.element.parentElement.getBoundingClientRect();
        const rowHeight = parseFloat(getComputedStyle(entry.element).getPropertyValue('--term-row-height'));
        const inset = keyboard ? Math.max(0, Math.min(parent.height - rowHeight, parent.bottom - viewport.offsetTop - viewport.height)) : 0;
        entry.element.style.setProperty('--terminal-keyboard-inset', inset + 'px');
        fitTerminal(entry);
    };
    const positionInputLine = (center = false) => {
        if (document.activeElement !== textarea || (typeof isSplit !== 'undefined' && isSplit)) return false;
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed) return false;
        const viewport = window.visualViewport;
        if (!viewport || viewport.scale !== 1 || viewport.height >= window.innerHeight - 100) return false;
        const cursor = entry.element.querySelector('.term-cursor');
        if (!cursor) return false;
        const rect = entry.element.getBoundingClientRect();
        const top = Math.max(rect.top, viewport.offsetTop);
        const bottom = Math.min(rect.bottom, viewport.offsetTop + viewport.height);
        if (bottom <= top) return false;
        const cursorRect = cursor.getBoundingClientRect();
        const grid = entry.element.querySelector('.term-grid');
        const rowHeight = parseFloat(getComputedStyle(entry.element).getPropertyValue('--term-row-height'));
        const atLastRow = grid.getBoundingClientRect().bottom - cursorRect.top <= rowHeight + 1;
        if (center && !atLastRow) {
            entry.element.scrollTop += cursorRect.top - (top + (bottom - top - rowHeight) / 2);
        } else if (cursorRect.top < top) {
            entry.element.scrollTop += cursorRect.top - top;
        } else if (cursorRect.top + rowHeight > bottom) {
            entry.element.scrollTop += cursorRect.top + rowHeight - bottom;
        }
        return true;
    };
    entry.positionInputLine = positionInputLine;
    const position = () => {
        if (document.activeElement !== textarea) return;
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed) return;
        if (typeof isSplit !== 'undefined' && isSplit) {
            const container = document.getElementById('terminal' + entry.pane + '-container');
            const rect = container.getBoundingClientRect();
            window.scrollBy({ top: rect.top - 2, behavior: 'smooth' });
            return;
        }
        entry.followInput = positionInputLine(true);
    };
    const schedule = () => {
        clearTimeout(entry.focusTimer);
        entry.focusTimer = setTimeout(() => requestAnimationFrame(position), 300);
    };
    textarea.addEventListener('focus', () => {
        if (typeof setActiveTerminal === 'function') setActiveTerminal(entry.pane, false);
        fitVisibleViewport();
        schedule();
    }, { signal });
    textarea.addEventListener('blur', () => {
        entry.followInput = false;
        clearTimeout(entry.focusTimer);
        fitVisibleViewport();
    }, { signal });
    for (const type of ['touchstart', 'wheel', 'pointerdown']) {
        entry.element.addEventListener(type, () => { entry.followInput = false; }, { signal, passive: true });
    }
    window.visualViewport?.addEventListener('resize', () => {
        fitVisibleViewport();
        if (window.visualViewport.height >= window.innerHeight - 100) {
            entry.followInput = false;
        } else schedule();
    }, { signal });
    window.visualViewport?.addEventListener('scroll', fitVisibleViewport, { signal });
    if (document.activeElement === textarea) {
        fitVisibleViewport();
        schedule();
    }
}

// --- Resize ---
let resizeTimeout;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(doFit, 100);
});
