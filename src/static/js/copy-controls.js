window.CopyControls = (() => {
    const copyIcon = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
    function button(readText, label) {
        const element = document.createElement('button');
        element.type = 'button';
        element.className = 'tc-copy-btn code-copy-button';
        element.setAttribute('aria-label', label);
        element.title = label;
        element.innerHTML = copyIcon;
        element.onclick = async event => {
            if (event) event.stopPropagation();
            await navigator.clipboard.writeText(readText());
            element.classList.add('copied');
            clearTimeout(element._copyReset);
            element._copyReset = setTimeout(() => { element.classList.remove('copied'); }, 1000);
        };
        return element;
    }
    function enhance(container) {
        if (container.tagName === 'PRE') {
            if (!container.querySelector('code')) return;
            if (container.parentElement.classList.contains('code-copy-container')) {
                container = container.parentElement;
            } else {
                const wrapper = document.createElement('div');
                wrapper.className = 'code-copy-container';
                container.before(wrapper);
                wrapper.appendChild(container);
                container = wrapper;
            }
        }
        if (container.querySelector(':scope > .code-copy-button')) return;
        const table = container.dataset.tableMarkdown !== undefined;
        container.appendChild(button(() => {
            const code = container.querySelector('code');
            return (table ? decodeURIComponent(container.dataset.tableMarkdown) : (code._fullText ?? code.textContent)).replace(/[\r\n]+$/, '');
        }, table ? 'Copy table Markdown' : 'Copy code'));
    }
    function observe(root) {
        const selector = 'pre, .markdown-table-copy';
        root.querySelectorAll(selector).forEach(enhance);
        const observer = new MutationObserver(records => {
            const containers = new Set();
            for (const record of records) {
                for (const node of record.addedNodes) {
                    if (node.nodeType !== Node.ELEMENT_NODE) continue;
                    if (node.matches(selector)) containers.add(node);
                    node.querySelectorAll(selector).forEach(element => containers.add(element));
                }
            }
            for (const container of containers) {
                if (root.contains(container)) enhance(container);
            }
        });
        observer.observe(root, {childList: true, subtree: true});
        root.addEventListener('pointerover', event => {
            if (event.pointerType !== 'mouse') return;
            const container = event.target.closest('.code-copy-container, .markdown-table-copy');
            if (!container || container.contains(event.relatedTarget)) return;
            container._copyHoverTimer = setTimeout(() => container.classList.add('code-copy-visible'), 1000);
        });
        root.addEventListener('pointerout', event => {
            const container = event.target.closest('.code-copy-container, .markdown-table-copy');
            if (!container || container.contains(event.relatedTarget)) return;
            clearTimeout(container._copyHoverTimer);
            container.classList.remove('code-copy-visible');
        });
        return observer;
    }
    return {button, observe};
})();
