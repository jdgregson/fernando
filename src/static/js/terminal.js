// --- Terminal Setup ---
const term1 = new Terminal({
    cursorBlink: true, fontSize: 14,
    fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    theme: { background: '#102d50', brightBlack: '#999999' }, scrollback: 10000, allowTransparency: false
});
const term2 = new Terminal({
    cursorBlink: true, fontSize: 14,
    fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    theme: { background: '#102d50', brightBlack: '#999999' }, scrollback: 10000, allowTransparency: false
});

term1.open(document.getElementById('terminal1'));
term2.open(document.getElementById('terminal2'));

const fitAddon1 = new FitAddon.FitAddon();
const fitAddon2 = new FitAddon.FitAddon();
term1.loadAddon(fitAddon1);
term1.loadAddon(new WebLinksAddon.WebLinksAddon());
term2.loadAddon(fitAddon2);
term2.loadAddon(new WebLinksAddon.WebLinksAddon());

function doFit() {
    if (document.hidden || typeof paneTypes === 'undefined') return;
    if (paneTypes[1] === 'terminal') {
        fitAddon1.fit();
        term1.resize(term1.cols + 1, term1.rows);
        emitWithCsrf('resize', { terminal: 1, rows: term1.rows, cols: term1.cols });
    }
    if (paneTypes[2] === 'terminal' && !document.getElementById('terminal2-container').classList.contains('hidden')) {
        fitAddon2.fit();
        term2.resize(term2.cols + 1, term2.rows);
        emitWithCsrf('resize', { terminal: 2, rows: term2.rows, cols: term2.cols });
    }
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) setTimeout(doFit, 100);
});

// --- Input ---
term1.onData(data => { emitWithCsrf('input', { terminal: 1, data: data }); });
term2.onData(data => { emitWithCsrf('input', { terminal: 2, data: data }); });

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
    if (data.terminal === 1) { if (output) term1.write(output); }
    else if (data.terminal === 2) { if (output) term2.write(output); }
});

// --- iOS touch scrolling ---
setTimeout(() => {
    document.querySelectorAll('.xterm').forEach((xtermEl, idx) => {
        const termNum = idx + 1;
        let startY = 0, lastY = 0, lastTime = 0, isScrolling = false, hasMoved = false, keyboardOpen = false;

        const textarea = xtermEl.querySelector('.xterm-helper-textarea');
        if (textarea) {
            textarea.addEventListener('focus', () => {
                if (typeof setActiveTerminal === 'function') setActiveTerminal(termNum);
                isScrolling = false;
                keyboardOpen = true;
                setTimeout(() => {
                    const viewport = xtermEl.querySelector('.xterm-viewport');
                    if (viewport) {
                        const scrollTop = viewport.scrollTop;
                        const scrollHeight = viewport.scrollHeight;
                        const clientHeight = viewport.clientHeight;
                        if (isSplit) {
                            const container = document.getElementById(`terminal${termNum}-container`);
                            const rect = container.getBoundingClientRect();
                            window.scrollBy({ top: rect.top - 2, behavior: 'smooth' });
                        } else {
                            if (scrollTop + clientHeight >= scrollHeight - 50) {
                                window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
                            }
                        }
                    }
                }, 300);
            });
            textarea.addEventListener('blur', () => { keyboardOpen = false; });
        }

        xtermEl.addEventListener('touchstart', (e) => {
            if (e.target.tagName === 'TEXTAREA' || keyboardOpen) return;
            startY = lastY = e.touches[0].clientY;
            lastTime = Date.now();
            hasMoved = false;
            isScrolling = false;
        }, { passive: false });

        xtermEl.addEventListener('touchmove', (e) => {
            if (e.target.tagName === 'TEXTAREA' || keyboardOpen) return;
            e.preventDefault();
            e.stopPropagation();
            const currentY = e.touches[0].clientY;
            const currentTime = Date.now();
            const deltaY = lastY - currentY;
            const deltaTime = Math.max(1, currentTime - lastTime);
            const velocity = Math.abs(deltaY) / deltaTime;
            if (Math.abs(startY - currentY) > 10) hasMoved = true;
            lastY = currentY;
            lastTime = currentTime;
            if (!isScrolling && hasMoved) {
                emitWithCsrf('input', { terminal: termNum, data: '\x02[' });
                isScrolling = true;
            }
            if (isScrolling && Math.abs(deltaY) > 0.5) {
                const multiplier = Math.min(3, 1 + velocity * 2);
                const lines = Math.max(1, Math.round(Math.abs(deltaY) * multiplier / 3));
                const key = deltaY > 0 ? '\x1b[B' : '\x1b[A';
                for (let i = 0; i < lines; i++) emitWithCsrf('input', { terminal: termNum, data: key });
            }
        }, { passive: false });

        xtermEl.addEventListener('touchend', (e) => {
            if (e.target.tagName === 'TEXTAREA' || keyboardOpen) return;
            if (isScrolling) {
                emitWithCsrf('input', { terminal: termNum, data: 'q' });
                isScrolling = false;
            }
        }, { passive: false });
    });
}, 100);

// --- Resize ---
let resizeTimeout;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(doFit, 100);
});

setTimeout(doFit, 100);
setTimeout(doFit, 300);
setTimeout(doFit, 500);
setTimeout(doFit, 1000);
