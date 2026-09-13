(() => {
    const messages = document.getElementById('messages');
    const dialog = document.createElement('dialog');
    dialog.className = 'image-viewer';
    dialog.setAttribute('aria-label', 'Image viewer');
    dialog.innerHTML = '<div class="image-viewer-toolbar"><button type="button" data-action="out" aria-label="Zoom out"><svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 10h12"/></svg></button><button type="button" data-action="in" aria-label="Zoom in"><svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 10h12M10 4v12"/></svg></button><button type="button" data-action="fit">Fit</button><button type="button" data-action="actual" aria-label="Actual size">1:1</button><span class="image-viewer-zoom" aria-live="polite"></span><button type="button" data-action="close" aria-label="Close image viewer"><svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 5 10 10M15 5 5 15"/></svg></button></div><div class="image-viewer-stage"><img alt="" draggable="false"></div>';
    document.body.appendChild(dialog);
    const stage = dialog.querySelector('.image-viewer-stage');
    const image = stage.querySelector('img');
    const label = dialog.querySelector('.image-viewer-zoom');
    const pointers = new Map();
    let scale = 1;
    let minimum = 1;
    let x = 0;
    let y = 0;
    let opener;
    let keyboardOpened = false;
    let tap = null;
    let tapTimer = null;

    function render() {
        const limitX = Math.max(0, (image.naturalWidth * scale - stage.clientWidth) / 2);
        const limitY = Math.max(0, (image.naturalHeight * scale - stage.clientHeight) / 2);
        x = Math.max(-limitX, Math.min(limitX, x));
        y = Math.max(-limitY, Math.min(limitY, y));
        image.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${scale})`;
        label.textContent = Math.round(scale * 100) + '%';
    }

    function fit() {
        if (!dialog.open || !image.naturalWidth || !stage.clientWidth || !stage.clientHeight) return;
        minimum = Math.min(1, stage.clientWidth / image.naturalWidth, stage.clientHeight / image.naturalHeight);
        scale = minimum;
        x = 0;
        y = 0;
        render();
    }

    function zoom(next, anchor = { x: 0, y: 0 }) {
        next = Math.max(minimum, Math.min(16, next));
        x = anchor.x - (anchor.x - x) * next / scale;
        y = anchor.y - (anchor.y - y) * next / scale;
        scale = next;
        render();
    }

    function point(event) {
        const rect = stage.getBoundingClientRect();
        return { x: event.clientX - rect.left - rect.width / 2, y: event.clientY - rect.top - rect.height / 2 };
    }

    function open(target, keyboard = false) {
        opener = target;
        keyboardOpened = keyboard;
        pointers.clear();
        tap = null;
        clearTimeout(tapTimer);
        dialog.classList.remove('controls-hidden');
        image.alt = target.alt || 'Expanded chat image';
        image.src = target.currentSrc || target.src;
        dialog.showModal();
        fit();
    }

    messages.addEventListener('click', event => {
        if (event.target.tagName !== 'IMG') return;
        event.preventDefault();
        event.stopPropagation();
        open(event.target);
    });
    messages.addEventListener('keydown', event => {
        if (event.target.tagName !== 'IMG' || !['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        open(event.target, true);
    });
    function prepare(root) {
        const images = root.matches?.('img') ? [root] : root.querySelectorAll?.('img') || [];
        for (const img of images) {
            img.tabIndex = 0;
            img.setAttribute('role', 'button');
            img.setAttribute('aria-label', 'Open image: ' + (img.alt || 'zoom and pan'));
        }
    }
    prepare(messages);
    new MutationObserver(records => {
        for (const record of records) for (const node of record.addedNodes) prepare(node);
    }).observe(messages, { childList: true, subtree: true });
    image.addEventListener('load', fit);
    new ResizeObserver(fit).observe(stage);
    dialog.addEventListener('close', () => {
        pointers.clear();
        tap = null;
        clearTimeout(tapTimer);
        image.removeAttribute('src');
        if (opener?.isConnected) {
            if (keyboardOpened) opener.focus({ preventScroll: true });
            else opener.blur();
        }
    });
    dialog.querySelector('.image-viewer-toolbar').addEventListener('click', event => {
        const action = event.target.closest('button')?.dataset.action;
        if (action === 'close') dialog.close();
        if (action === 'fit') fit();
        if (action === 'actual') zoom(1);
        if (action === 'in') zoom(scale * 1.5);
        if (action === 'out') zoom(scale / 1.5);
    });
    dialog.addEventListener('keydown', event => {
        if (['+', '=', '-', '0'].includes(event.key)) {
            event.preventDefault();
            if (event.key === '0') fit();
            else zoom(scale * (event.key === '-' ? 1 / 1.5 : 1.5));
        }
    });
    stage.addEventListener('wheel', event => {
        event.preventDefault();
        const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1);
        zoom(scale * Math.exp(-delta * 0.002), point(event));
    }, { passive: false });
    stage.addEventListener('dblclick', event => {
        event.preventDefault();
        clearTimeout(tapTimer);
        if (scale > minimum * 1.01) fit();
        else zoom(Math.max(1, minimum * 2), point(event));
    });
    stage.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        stage.setPointerCapture(event.pointerId);
        pointers.set(event.pointerId, point(event));
        tap = pointers.size === 1 ? { ...point(event), id: event.pointerId, time: performance.now() } : null;
        if (pointers.size > 1) clearTimeout(tapTimer);
    });
    stage.addEventListener('pointermove', event => {
        if (!pointers.has(event.pointerId)) return;
        const before = [...pointers.values()];
        const previous = pointers.get(event.pointerId);
        const current = point(event);
        if (tap && Math.hypot(current.x - tap.x, current.y - tap.y) > 8) tap = null;
        pointers.set(event.pointerId, current);
        const after = [...pointers.values()];
        if (pointers.size === 1) {
            x += current.x - previous.x;
            y += current.y - previous.y;
        } else if (pointers.size === 2) {
            const distance = points => Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y);
            const middle = points => ({ x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 });
            const oldMiddle = middle(before);
            const newMiddle = middle(after);
            if (distance(before) > 0) zoom(scale * distance(after) / distance(before), oldMiddle);
            x += newMiddle.x - oldMiddle.x;
            y += newMiddle.y - oldMiddle.y;
        }
        render();
    });
    stage.addEventListener('pointerup', event => {
        const current = point(event);
        if (tap?.id === event.pointerId && performance.now() - tap.time < 400 && Math.hypot(current.x - tap.x, current.y - tap.y) <= 8) {
            clearTimeout(tapTimer);
            tapTimer = setTimeout(() => dialog.classList.toggle('controls-hidden'), 250);
        }
        tap = null;
        pointers.delete(event.pointerId);
    });
    for (const name of ['pointercancel', 'lostpointercapture']) {
        stage.addEventListener(name, event => {
            tap = null;
            pointers.delete(event.pointerId);
        });
    }
})();
