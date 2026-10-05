(() => {
    const script = document.currentScript;
    const parentOrigin = new URL(script.src).origin;
    const listeners = new Map();
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
