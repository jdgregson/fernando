(function(root) {
    function createMcpAlert(element) {
        let revision = -1;
        let epoch;
        let rendered;
        return function render(state) {
            if (!state || !Number.isInteger(state.revision)) return;
            if (state.epoch !== epoch) {
                epoch = state.epoch;
                revision = -1;
            }
            if (state.revision < revision) return;
            revision = state.revision;
            const failures = state.failures || [];
            const signature = JSON.stringify(failures);
            if (signature === rendered) return;
            rendered = signature;
            element.replaceChildren();
            element.hidden = failures.length === 0;
            if (element.hidden) return;
            const heading = document.createElement('strong');
            heading.textContent = 'MCP SERVER LOAD FAILURE';
            const header = document.createElement('div');
            header.className = 'mcp-alert-header';
            header.appendChild(heading);
            const dismiss = document.createElement('button');
            dismiss.type = 'button';
            dismiss.className = 'tool-modal-close mcp-alert-dismiss';
            dismiss.setAttribute('aria-label', 'Dismiss MCP load failure');
            dismiss.innerHTML = '<svg width="14" height="14" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>';
            dismiss.onclick = () => { element.hidden = true; };
            header.appendChild(dismiss);
            element.appendChild(header);
            const explanation = document.createElement('div');
            explanation.textContent = 'This chat was configured to load these servers, but their tools are unavailable or startup could not be verified.';
            element.appendChild(explanation);
            const list = document.createElement('ul');
            for (const failure of failures) {
                const item = document.createElement('li');
                const name = document.createElement('strong');
                name.textContent = failure.name + (failure.status === 'unconfirmed' ? ' (startup unconfirmed): ' : ': ');
                item.appendChild(name);
                item.appendChild(document.createTextNode(failure.error));
                list.appendChild(item);
            }
            element.appendChild(list);
        };
    }
    root.createMcpAlert = createMcpAlert;
})(typeof window === 'undefined' ? module.exports : window);
