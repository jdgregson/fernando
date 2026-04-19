// --- Terminal Setup (wterm) ---
// Each session gets its own WTerm instance. Panes show/hide them on switch.

// session_name -> { wterm, element, pane, ready, pending }
const termInstances = {};

// pane -> session_name (for routing output from server)
const _paneSession = { 1: null, 2: null };

// Measure container to get correct initial cols/rows using wterm's font styles.
function measureTermSize(container) {
    container.classList.add('wterm');
    const style = getComputedStyle(container);
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-family:' +
        style.getPropertyValue('--term-font-family') + ';font-size:' +
        style.getPropertyValue('--term-font-size') + ';line-height:' +
        style.getPropertyValue('--term-line-height') + ';';
    probe.textContent = 'W';
    container.appendChild(probe);
    const rect = probe.getBoundingClientRect();
    probe.remove();
    container.classList.remove('wterm');
    if (!rect.width || !rect.height) return null;
    const pad = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
    const padV = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
    const w = container.clientWidth - pad;
    const h = container.clientHeight - padV;
    return { cols: Math.max(1, Math.floor(w / rect.width)), rows: Math.max(1, Math.floor(h / rect.height)) };
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

    const size = measureTermSize(container);
    const wterm = new WTerm(el, Object.assign({
        autoResize: true,
        cursorBlink: true,
        onResize: (cols, rows) => { emitWithCsrf('resize', { terminal: entry.pane, rows, cols }); },
    }, size || {}));
    wterm.onData = data => { emitWithCsrf('input', { terminal: entry.pane, data }); };

    entry = { wterm, element: el, pane, ready: false, pending: [], firstAttach: true };
    termInstances[sessionName] = entry;

    wterm.init().then(() => {
        entry.ready = true;
        if (entry.pending.length) {
            entry.pending.forEach(d => wterm.write(d));
            entry.pending = [];
        }
        requestAnimationFrame(() => wterm.scrollToBottom());
        // iOS WebKit paint bug: inject spacer after first paint
        if (/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
            setTimeout(() => {
                const grid = el.querySelector('.term-grid');
                if (grid) {
                    const spacer = document.createElement('div');
                    spacer.className = 'ios-spacer';
                    spacer.style.height = '10px';
                    el.insertBefore(spacer, grid);
                }
            }, 100);
        }
        setupFocusScroll(el, pane);
    }).catch(err => console.error('wterm init failed for', sessionName, err));

    return entry;
}

// Show a session's terminal in a pane, hide all others in that pane.
function showTermInPane(sessionName, pane) {
    const container = document.getElementById('terminal' + pane);
    // Hide all terminals in this pane — disconnect ResizeObserver first to prevent
    // 0-width resize that corrupts scrollback, then move offscreen
    for (const [name, inst] of Object.entries(termInstances)) {
        if (inst.pane === pane && name !== sessionName) {
            if (inst.wterm.resizeObserver) inst.wterm.resizeObserver.disconnect();
            inst.element.style.cssText = 'position:fixed;left:-9999px;visibility:hidden;';
        }
    }
    const entry = getOrCreateTerm(sessionName, pane);
    entry.element.style.cssText = '';
    // Reconnect ResizeObserver
    if (entry.wterm.resizeObserver) entry.wterm.resizeObserver.observe(entry.wterm.element);
    if (entry.ready) {
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
    entry.wterm.destroy();
    entry.element.remove();
    delete termInstances[sessionName];
}

// Get the active WTerm for a pane
function getTermForPane(pane) {
    const session = pane === 1 ? currentSession1 : currentSession2;
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
    if (document.hidden || typeof paneTypes === 'undefined') return;
    if (paneTypes[1] === 'terminal') {
        const t = getTermForPane(1);
        if (t) emitWithCsrf('resize', { terminal: 1, rows: t.rows, cols: t.cols });
    }
    if (paneTypes[2] === 'terminal' && !document.getElementById('terminal2-container').classList.contains('hidden')) {
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

function setupFocusScroll(wtermEl, pane) {
    const textarea = wtermEl.querySelector('textarea');
    if (!textarea) return;
    textarea.addEventListener('focus', () => {
        if (typeof setActiveTerminal === 'function') setActiveTerminal(pane, false);
        setTimeout(() => {
            if (typeof isSplit !== 'undefined' && isSplit) {
                const container = document.getElementById('terminal' + pane + '-container');
                const rect = container.getBoundingClientRect();
                window.scrollBy({ top: rect.top - 2, behavior: 'smooth' });
            } else {
                window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
            }
        }, 300);
    });
}

// --- Resize ---
let resizeTimeout;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(doFit, 100);
});
