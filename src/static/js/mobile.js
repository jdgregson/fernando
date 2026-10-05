// --- Mobile Controls ---
function isDesktopActive() {
    return paneController.isEmbedded(activeTerminal, 'desktop');
}

function updateMobileControls() {
    const mc = document.getElementById('mobileControls');
    const active = paneController.activeAdapter();
    for (const className of new Set([...paneController.sessionTypes.values()].map(adapter => adapter.mobileClass).filter(Boolean))) {
        mc.classList.toggle(className, active?.mobileClass === className);
    }
}

function sendKey(key, desktopKey) {
    const adapter = paneController.activeAdapter();
    if (desktopKey && adapter?.sendKey) {
        adapter.sendKey(key, desktopKey);
    } else {
        emitWithCsrf('input', { terminal: activeTerminal, data: key });
    }
}

function submitDictation() {
    const input = document.getElementById('dictationInput');
    const text = input.value;
    if (text) {
        emitWithCsrf('input', { terminal: activeTerminal, data: text + '\r' });
        input.value = '';
    }
    setTimeout(() => input.focus(), 50);
}

document.getElementById('dictationInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); submitDictation(); }
});

// --- Reposition above keyboard ---
if (window.visualViewport) {
    const mobileControls = document.querySelector('.mobile-controls');
    let keyboardVisible = false;

    const updatePosition = () => {
        const vpHeight = window.visualViewport.height;
        const vpOffsetTop = window.visualViewport.offsetTop;
        const windowHeight = window.innerHeight;
        keyboardVisible = vpHeight < windowHeight - 100;
        if (keyboardVisible) {
            mobileControls.style.bottom = 'auto';
            mobileControls.style.top = (vpOffsetTop + vpHeight - mobileControls.offsetHeight) + 'px';
            mobileControls.style.transform = 'translateX(-50%)';
        } else {
            mobileControls.style.top = '';
            mobileControls.style.bottom = '0';
            mobileControls.style.transform = 'translateX(-50%) translateY(50%)';
        }
    };

    const updatePositionOnly = () => {
        if (keyboardVisible) {
            mobileControls.style.top = (window.visualViewport.offsetTop + window.visualViewport.height - mobileControls.offsetHeight) + 'px';
        }
    };

    window.visualViewport.addEventListener('resize', updatePosition);
    window.visualViewport.addEventListener('scroll', updatePositionOnly);
}
