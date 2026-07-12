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
    socket.emit('request_git_status');
    setTimeout(() => onSocketConnected(), 0);
});

socket.on('git_dirty', (data) => {
    const dot = document.getElementById('gitDirtyDot');
    if (dot) dot.style.display = data.dirty ? 'inline' : 'none';
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
    loadAuthConfig();
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
            const effortSel = document.getElementById('settingsEffort');
            if (effortSel) effortSel.value = data.default_effort || 'max';
        }).catch(() => {});
}

function saveDefaultModel(value) {
    fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({key: 'default_model', value})
    }).catch(() => {});
}

function saveDefaultEffort(value) {
    fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({key: 'default_effort', value})
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

function loadAuthConfig() {
    fetch('/api/authorization/config?api_key=' + window.FERNANDO_API_KEY)
        .then(r => r.json())
        .then(config => {
            const container = document.getElementById('authConfigArea');
            const auths = config.authorizations || {};
            const names = Object.keys(auths);
            let html = '';
            for (const name of names) {
                const a = auths[name];
                html += `<div class="mcp-server-item" style="flex-direction:column;align-items:stretch;gap:6px;padding:10px 12px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div class="mcp-server-name">${name}</div>
                        <button onclick="removeAuth('${name}')" style="background:transparent;color:#f44;border:none;cursor:pointer;font-size:11px;">Remove</button>
                    </div>
                    <div class="settings-row" style="margin:0;gap:8px;">
                        <label class="settings-label" style="min-width:90px;">Description</label>
                        <input type="text" class="settings-select" data-auth="${name}" data-field="description" value="${a.description || ''}" style="flex:1;">
                    </div>
                    <div class="settings-row" style="margin:0;gap:8px;">
                        <label class="settings-label" style="min-width:90px;">Match command</label>
                        <input type="text" class="settings-select" data-auth="${name}" data-field="match_command" value="${a.match_command || ''}" style="flex:1;">
                    </div>
                    <div style="display:flex;align-items:center;gap:6px;margin-top:2px;">
                        <label style="color:#8899aa;font-size:12px;">Timeout (sec)</label>
                        <input type="number" class="settings-select" data-auth="${name}" data-field="timeout_seconds" value="${a.timeout_seconds || 300}" style="width:70px;">
                    </div>
                    <div style="display:flex;align-items:center;gap:6px;">
                        <input type="checkbox" data-auth="${name}" data-field="expire_on_use" ${a.expire_on_use ? 'checked' : ''}>
                        <label style="color:#8899aa;font-size:12px;">Single-use</label>
                    </div>
                </div>`;
            }
            html += `<div style="margin-top:8px;display:flex;gap:8px;">
                <input type="text" id="newAuthName" placeholder="New authorization name" class="settings-select" style="flex:1;">
                <button onclick="addAuth()" style="background:#2d6b4f;color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:12px;">Add</button>
            </div>`;
            html += `<button onclick="saveAuthConfigFromEditor()" style="margin-top:10px;background:#2d6b4f;color:#fff;border:none;border-radius:4px;padding:6px 14px;cursor:pointer;font-size:12px;">Save</button>`;
            container.innerHTML = html;
        })
        .catch(() => {
            document.getElementById('authConfigArea').textContent = 'Failed to load auth config.';
        });
}

function removeAuth(name) {
    const el = document.querySelector(`[data-auth="${name}"]`).closest('.mcp-server-item');
    el.remove();
}

function addAuth() {
    const nameInput = document.getElementById('newAuthName');
    const name = nameInput.value.trim();
    if (!name) return;
    nameInput.value = '';
    const container = document.getElementById('authConfigArea');
    const addDiv = container.querySelector('div[style*="margin-top:8px"]');
    const newItem = document.createElement('div');
    newItem.className = 'mcp-server-item';
    newItem.style.cssText = 'flex-direction:column;align-items:stretch;gap:6px;padding:10px 12px;';
    newItem.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <div class="mcp-server-name">${name}</div>
            <button onclick="removeAuth('${name}')" style="background:transparent;color:#f44;border:none;cursor:pointer;font-size:11px;">Remove</button>
        </div>
        <div class="settings-row" style="margin:0;gap:8px;">
            <label class="settings-label" style="min-width:90px;">Description</label>
            <input type="text" class="settings-select" data-auth="${name}" data-field="description" value="" style="flex:1;">
        </div>
        <div class="settings-row" style="margin:0;gap:8px;">
            <label class="settings-label" style="min-width:90px;">Match command</label>
            <input type="text" class="settings-select" data-auth="${name}" data-field="match_command" value="" style="flex:1;">
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-top:2px;">
            <label style="color:#8899aa;font-size:12px;">Timeout (sec)</label>
            <input type="number" class="settings-select" data-auth="${name}" data-field="timeout_seconds" value="300" style="width:70px;">
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
            <input type="checkbox" data-auth="${name}" data-field="expire_on_use" checked>
            <label style="color:#8899aa;font-size:12px;">Single-use</label>
        </div>
    `;
    container.insertBefore(newItem, addDiv);
}

function saveAuthConfigFromEditor() {
    const auths = {};
    const items = document.querySelectorAll('#authConfigArea .mcp-server-item');
    items.forEach(item => {
        const inputs = item.querySelectorAll('[data-auth]');
        if (!inputs.length) return;
        const name = inputs[0].dataset.auth;
        auths[name] = {};
        inputs.forEach(inp => {
            const field = inp.dataset.field;
            if (inp.type === 'checkbox') auths[name][field] = inp.checked;
            else if (inp.type === 'number') auths[name][field] = parseInt(inp.value) || 0;
            else auths[name][field] = inp.value;
        });
    });
    fetch('/api/authorization/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({authorizations: auths}),
    }).then(() => loadAuthConfig());
}