(() => {
    const script = document.currentScript;
    const parentOrigin = new URL(script.src).origin;
    const listeners = new Map();
    const selectionStyles = document.createElement('link');
    selectionStyles.rel = 'stylesheet';
    selectionStyles.href = new URL('../css/group-selection.css', script.src).href;
    document.head.appendChild(selectionStyles);
    const updateSelection = payload => {
        const root = document.documentElement;
        const color = payload?.group?.color;
        const grouped = /^#[0-9a-f]{6}$/i.test(color);
        root.toggleAttribute('data-group-selection', grouped);
        if (!grouped) {
            root.style.removeProperty('--group-selection-background');
            root.style.removeProperty('--group-selection-foreground');
            return;
        }
        const channels = color.slice(1).match(/../g).map(channel => {
            const value = parseInt(channel, 16) / 255;
            return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        });
        const luminance = channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
        root.style.setProperty('--group-selection-background', color);
        root.style.setProperty('--group-selection-foreground', luminance > 0.179 ? '#000000' : '#ffffff');
    };
    listeners.set('context', new Set([updateSelection]));
    listeners.set('state', new Set([updateSelection]));
    const send = (event, payload = {}) => {
        if (window.parent !== window) window.parent.postMessage({ type: 'fernando-pane', version: 1, event, payload }, parentOrigin);
    };
    window.FernandoPane = Object.freeze({
        activate: () => send('activate'),
        navigate: location => send('navigate', location),
        requestContext: () => send('context-request'),
        on(event, callback) {
            if (!listeners.has(event)) listeners.set(event, new Set());
            listeners.get(event).add(callback);
            return () => listeners.get(event).delete(callback);
        },
    });
    window.addEventListener('message', event => {
        if (event.source !== window.parent || event.origin !== parentOrigin) return;
        const message = event.data;
        if (!message || message.type !== 'fernando-pane' || message.version !== 1 || typeof message.event !== 'string') return;
        for (const callback of listeners.get(message.event) || []) callback(message.payload);
    });
    const activateOn = script.dataset.activateOn;
    if (activateOn) {
        if (!['click', 'mousedown', 'pointerdown'].includes(activateOn)) throw new Error('Unsupported pane activation event');
        document.addEventListener(activateOn, window.FernandoPane.activate, {capture: script.dataset.activateCapture === 'true', passive: true});
    }
    send('ready');
})();
