// Generic scroll pill system — hides native scrollbars, draws custom overlay pills
(function() {
    const pillMap = new WeakMap();

    function attachScrollPill(el) {
        if (pillMap.has(el)) return;
        if (!el.offsetParent && el !== document.body && el !== document.documentElement) return;
        const parent = el.parentElement;
        if (!parent) return;
        if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
        const pill = document.createElement('div');
        pill.className = 'scroll-pill';
        parent.appendChild(pill);
        let timer = null;
        let hovered = false;
        let dragging = false;
        let dragStartY, dragStartScroll;

        function update() {
            if (el.scrollHeight <= el.clientHeight + 1) { pill.classList.remove('visible'); return; }
            const ratio = el.clientHeight / el.scrollHeight;
            const pillHeight = Math.max(24, ratio * el.clientHeight);
            const scrollRatio = el.scrollTop / (el.scrollHeight - el.clientHeight);
            const pillTop = el.offsetTop + scrollRatio * (el.clientHeight - pillHeight);
            pill.style.height = pillHeight + 'px';
            pill.style.top = pillTop + 'px';
            pill.classList.add('visible');
            clearTimeout(timer);
            if (!hovered && !dragging) timer = setTimeout(() => pill.classList.remove('visible'), 1000);
        }

        el.addEventListener('scroll', update);
        pill.addEventListener('mouseenter', () => { hovered = true; clearTimeout(timer); pill.classList.add('visible'); });
        pill.addEventListener('mouseleave', () => { hovered = false; if (!dragging) timer = setTimeout(() => pill.classList.remove('visible'), 600); });
        function startDrag(startY) {
            dragging = true;
            pill.classList.add('dragging');
            dragStartY = startY;
            dragStartScroll = el.scrollTop;
        }
        function moveDrag(currentY) {
            const dy = currentY - dragStartY;
            const scrollRange = el.scrollHeight - el.clientHeight;
            const trackRange = el.clientHeight - pill.offsetHeight;
            el.scrollTop = dragStartScroll + (dy / trackRange) * scrollRange;
        }
        function endDrag() {
            dragging = false;
            pill.classList.remove('dragging');
            if (!hovered) timer = setTimeout(() => pill.classList.remove('visible'), 600);
        }
        pill.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startDrag(e.clientY);
            const onMove = (ev) => moveDrag(ev.clientY);
            const onUp = () => { endDrag(); document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
        pill.addEventListener('touchstart', (e) => {
            e.preventDefault();
            startDrag(e.touches[0].clientY);
            pill.classList.add('visible');
            const onMove = (ev) => { ev.preventDefault(); moveDrag(ev.touches[0].clientY); };
            const onEnd = () => { endDrag(); pill.removeEventListener('touchmove', onMove); pill.removeEventListener('touchend', onEnd); pill.removeEventListener('touchcancel', onEnd); };
            pill.addEventListener('touchmove', onMove, { passive: false });
            pill.addEventListener('touchend', onEnd);
            pill.addEventListener('touchcancel', onEnd);
        }, { passive: false });
        pillMap.set(el, pill);
    }

    function scanScrollables() {
        document.querySelectorAll('*').forEach(el => {
            if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.closest('.textarea-wrap')) return;
            const style = getComputedStyle(el);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1) {
                attachScrollPill(el);
            }
        });
    }

    scanScrollables();
    new MutationObserver(scanScrollables).observe(document.body, { childList: true, subtree: true });
    window._attachScrollPill = attachScrollPill;
})();
