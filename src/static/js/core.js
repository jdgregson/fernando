// --- Utilities ---
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function showAlert(message) {
    return new Promise(resolve => {
        document.getElementById('alertMessage').textContent = message;
        document.getElementById('alertButtons').innerHTML = '<button onclick="closeAlert()">OK</button>';
        document.getElementById('alertModal').classList.add('open');
        window.alertResolve = resolve;
    });
}

function showConfirm(message) {
    return new Promise(resolve => {
        document.getElementById('alertMessage').textContent = message;
        document.getElementById('alertButtons').innerHTML =
            '<button class="cancel" onclick="closeAlert(false)">Cancel</button><button onclick="closeAlert(true)">OK</button>';
        document.getElementById('alertModal').classList.add('open');
        window.alertResolve = resolve;
    });
}

function closeAlert(result) {
    document.getElementById('alertModal').classList.remove('open');
    if (window.alertResolve) {
        window.alertResolve(result);
        window.alertResolve = null;
    }
}

function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2000);
}

// --- Socket ---
const socket = io({
    path: window.location.pathname.replace(/\/$/, '') + '/socket.io',
    query: { api_key: window.FERNANDO_API_KEY },
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionAttempts: 60
});

let csrfToken = null;
let isMutating = false;

document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !socket.connected && !isMutating) {
        fetch('/api/auth_check', { headers: { 'X-API-Key': apiKey } })
            .then(r => { if (r.status === 401) window.location.reload(); })
            .catch(() => {});
    }
});

function emitWithCsrf(event, data = {}) {
    socket.emit(event, { ...data, csrf_token: csrfToken });
}

socket.on('mutating', () => {
    isMutating = true;
    // Clear notes iframe to prevent stale auth alerts during restart
    [1, 2].forEach(n => {
        const b = document.getElementById('browser' + n);
        if (b) {
            const iframe = b.querySelector('iframe');
            if (iframe && iframe.src && iframe.src.includes('/notes/')) {
                iframe.removeAttribute('src');
                iframe.srcdoc = '<html style="background:#0d2848"></html>';
            }
        }
    });
});

socket.on('connected', (data) => {
    csrfToken = data.csrf_token;
    console.log('Connected with CSRF token');
    setTimeout(() => onSocketConnected(), 0);
});

socket.on('disconnect', () => {
    console.log('Socket disconnected');
    window._mutateTimer = setTimeout(() => {
        const overlay = document.getElementById('mutateOverlay');
        const spinner = document.getElementById('overlaySpinner');
        const icon = document.getElementById('overlayIcon');
        const label = document.getElementById('overlayLabel');
        if (isMutating) {
            spinner.style.display = 'none';
            icon.style.display = '';
            icon.textContent = '🧬';
            icon.classList.add('spin');
            label.textContent = 'mutating...';
            window._mutatePoll = setInterval(() => {
                fetch('/', { method: 'HEAD' }).then(r => {
                    if (r.ok) {
                        clearInterval(window._mutatePoll);
                        window.location.reload();
                    }
                }).catch(() => {});
            }, 1500);
        } else {
            spinner.style.display = '';
            icon.style.display = 'none';
            label.textContent = 'connecting...';
        }
        overlay.classList.add('open');
    }, 800);
});

socket.io.on('reconnect', () => {
    console.log('Socket reconnected');
    clearTimeout(window._mutateTimer);
    clearInterval(window._mutatePoll);
    if (isMutating) {
        window.location.reload();
        return;
    }
    document.getElementById('mutateOverlay').classList.remove('open');
});

function openSettings() {
    document.getElementById('settingsModal').classList.add('open');
    loadSettings();
    loadMcpServers();
}
function closeSettings() { document.getElementById('settingsModal').classList.remove('open'); }
function switchSettingsTab(tab, btn) {
    document.querySelectorAll('#settingsModal .sa-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.settings-tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('settingsTab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
}


function loadSettings() {
    fetch('/api/settings?api_key=' + window.FERNANDO_API_KEY)
        .then(r => r.json())
        .then(data => {
            const sel = document.getElementById('settingsModel');
            const currentModel = data.default_model || '';
            // Populate model dropdown from API
            fetch('/api/models?api_key=' + window.FERNANDO_API_KEY)
                .then(r => r.json())
                .then(mdata => {
                    if (mdata.models && sel) {
                        sel.innerHTML = '';
                        mdata.models.forEach(m => {
                            const opt = document.createElement('option');
                            opt.value = m.model_id;
                            opt.textContent = m.model_name;
                            sel.appendChild(opt);
                        });
                        if (currentModel) sel.value = currentModel;
                    }
                }).catch(() => {});
        }).catch(() => {});
}

function saveDefaultModel(value) {
    fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({key: 'default_model', value})
    }).catch(() => {});
}

function loadMcpServers() {
    fetch('/api/mcp/bundled?api_key=' + window.FERNANDO_API_KEY)
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById('mcpServerList');
            if (!data.servers || !data.servers.length) {
                container.textContent = 'No bundled MCP servers found.';
                return;
            }
            container.innerHTML = data.servers.map(s =>
                `<div class="mcp-server-item">
                    <input type="checkbox" id="mcp_${s.name}" ${s.enabled ? 'checked' : ''} onchange="toggleMcpServer('${s.name}', this.checked)">
                    <label for="mcp_${s.name}"><div class="mcp-server-name">${s.name}</div><div class="mcp-server-desc">${s.description}</div></label>
                </div>`
            ).join('');
        })
        .catch(() => {
            document.getElementById('mcpServerList').textContent = 'Failed to load MCP servers.';
        });
}

function toggleMcpServer(name, enabled) {
    fetch('/api/mcp/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({name, enabled})
    }).then(r => r.json()).then(data => {
        if (data.error) {
            alert('Error: ' + data.error);
            loadMcpServers();
        }
    }).catch(() => { loadMcpServers(); });
}