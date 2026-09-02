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
            // Load Kiro models
            const kiroSel = document.getElementById('settingsKiroModel');
            const currentKiroModel = data.default_model || '';
            fetch('/api/models?api_key=' + window.FERNANDO_API_KEY)
                .then(r => r.json())
                .then(mdata => {
                    if (mdata.models && kiroSel) {
                        kiroSel.innerHTML = '';
                        mdata.models.forEach(m => {
                            const opt = document.createElement('option');
                            opt.value = m.model_id;
                            opt.textContent = m.model_name;
                            kiroSel.appendChild(opt);
                        });
                        if (currentKiroModel) kiroSel.value = currentKiroModel;
                    }
                }).catch(() => {});
            // Load OpenCode models
            const openCodeSel = document.getElementById('settingsOpenCodeModel');
            const currentOpenCodeModel = data.opencode_model || '';
            fetch('/api/opencode_models?api_key=' + window.FERNANDO_API_KEY)
                .then(r => r.json())
                .then(mdata => {
                    if (mdata.models && openCodeSel) {
                        openCodeSel.innerHTML = '';
                        mdata.models.forEach(m => {
                            const opt = document.createElement('option');
                            opt.value = m;
                            opt.textContent = m;
                            openCodeSel.appendChild(opt);
                        });
                        if (currentOpenCodeModel) openCodeSel.value = currentOpenCodeModel;
                    }
                }).catch(() => {
                    if (openCodeSel) openCodeSel.innerHTML = '<option value="">Not available</option>';
                });
            const effortSel = document.getElementById('settingsEffort');
            if (effortSel) effortSel.value = data.default_effort || 'max';
            const providerOpenCode = document.getElementById('providerOpenCode');
            if (providerOpenCode) providerOpenCode.checked = data.providers_opencode === true;
        }).catch(() => {});
}

function saveOpenCodeModel(value) {
    fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({key: 'opencode_model', value})
    }).catch(() => {});
}

function saveProviderSetting(provider, enabled) {
    fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
        body: JSON.stringify({key: 'providers_' + provider, value: enabled})
    }).then(() => {
        applyProviderSettings();
    }).catch(() => {});
}

function applyProviderSettings() {
    fetch('/api/settings?api_key=' + window.FERNANDO_API_KEY)
        .then(r => r.json())
        .then(data => {
            const openCodeTile = document.getElementById('openCodeTile');
            if (openCodeTile) {
                openCodeTile.style.display = data.providers_opencode === true ? '' : 'none';
            }
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
                html += `<div class="auth-card" data-auth-card="${name}">
                    <div class="auth-card-header">
                        <div class="auth-card-name">${name}</div>
                        <button class="auth-card-remove" onclick="removeAuth('${name}')">Remove</button>
                    </div>
                    <div class="auth-card-row">
                        <label class="auth-card-label">Description</label>
                        <input type="text" class="auth-card-input" data-auth="${name}" data-field="description" value="${a.description || ''}" onchange="saveAuthConfig()">
                    </div>
                    <div class="auth-card-row">
                        <label class="auth-card-label">Match command</label>
                        <input type="text" class="auth-card-input" data-auth="${name}" data-field="match_command" value="${a.match_command || ''}" onchange="saveAuthConfig()">
                    </div>
                    <div class="auth-card-row">
                        <label class="auth-card-label">Timeout (sec)</label>
                        <input type="number" class="auth-card-input" data-auth="${name}" data-field="timeout_seconds" value="${a.timeout_seconds || 300}" onchange="saveAuthConfig()">
                    </div>
                    <div class="auth-card-checkbox-row">
                        <input type="checkbox" data-auth="${name}" data-field="expire_on_use" ${a.expire_on_use ? 'checked' : ''} onchange="saveAuthConfig()">
                        <label class="auth-card-label" style="min-width:auto;">Single-use (expire after first use)</label>
                    </div>
                </div>`;
            }
            html += `<div class="auth-add-row">
                <input type="text" id="newAuthName" placeholder="New authorization name" class="auth-card-input" style="flex:1;">
                <button class="auth-add-btn" onclick="addAuth()">Add</button>
            </div>`;
            container.innerHTML = html;
        })
        .catch(() => {
            document.getElementById('authConfigArea').textContent = 'Failed to load auth config.';
        });
}

function removeAuth(name) {
    const el = document.querySelector(`[data-auth-card="${name}"]`);
    if (el) el.remove();
    saveAuthConfig();
}

function addAuth() {
    const nameInput = document.getElementById('newAuthName');
    const name = nameInput.value.trim();
    if (!name) return;
    nameInput.value = '';
    const container = document.getElementById('authConfigArea');
    const addRow = container.querySelector('.auth-add-row');
    const newItem = document.createElement('div');
    newItem.className = 'auth-card';
    newItem.setAttribute('data-auth-card', name);
    newItem.innerHTML = `
        <div class="auth-card-header">
            <div class="auth-card-name">${name}</div>
            <button class="auth-card-remove" onclick="removeAuth('${name}')">Remove</button>
        </div>
        <div class="auth-card-row">
            <label class="auth-card-label">Description</label>
            <input type="text" class="auth-card-input" data-auth="${name}" data-field="description" value="" onchange="saveAuthConfig()">
        </div>
        <div class="auth-card-row">
            <label class="auth-card-label">Match command</label>
            <input type="text" class="auth-card-input" data-auth="${name}" data-field="match_command" value="" onchange="saveAuthConfig()">
        </div>
        <div class="auth-card-row">
            <label class="auth-card-label">Timeout (sec)</label>
            <input type="number" class="auth-card-input" data-auth="${name}" data-field="timeout_seconds" value="300" onchange="saveAuthConfig()">
        </div>
        <div class="auth-card-checkbox-row">
            <input type="checkbox" data-auth="${name}" data-field="expire_on_use" checked onchange="saveAuthConfig()">
            <label class="auth-card-label" style="min-width:auto;">Single-use (expire after first use)</label>
        </div>
    `;
    container.insertBefore(newItem, addRow);
    saveAuthConfig();
}

function saveAuthConfig() {
    const auths = {};
    const items = document.querySelectorAll('#authConfigArea .auth-card');
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
    }).catch(() => {});
}

// --- Health Monitoring ---
let healthModalOpen = false;
let healthPollInterval = null;

function openHealthModal() {
    document.getElementById('healthModal').classList.add('open');
    healthModalOpen = true;
    fetchHealth();
    if (!healthPollInterval) {
        healthPollInterval = setInterval(fetchHealth, 5000);
    }
}

function closeHealthModal() {
    document.getElementById('healthModal').classList.remove('open');
    healthModalOpen = false;
}

function fetchHealth() {
    fetch('/api/health?api_key=' + window.FERNANDO_API_KEY)
        .then(r => r.json())
        .then(data => {
            updateHealthIndicator(data);
            if (healthModalOpen) {
                updateHealthModal(data);
            }
        })
        .catch(err => {
            console.error('Health fetch error:', err);
        });
}

function updateHealthIndicator(data) {
    const dot = document.getElementById('healthDot');
    if (!dot) return;
    if (data.status === 'unhealthy') {
        dot.classList.add('unhealthy');
        dot.title = 'System unhealthy: ' + (data.reasons || []).join(', ');
    } else {
        dot.classList.remove('unhealthy');
        dot.title = 'System healthy';
    }
}

function updateHealthModal(data) {
    const banner = document.getElementById('healthStatusBanner');
    const icon = document.getElementById('healthStatusIcon');
    const text = document.getElementById('healthStatusText');
    const reasons = document.getElementById('healthReasons');

    if (data.status === 'unhealthy') {
        banner.className = 'health-status-banner unhealthy';
        icon.innerHTML = '&#10007;';
        text.textContent = 'System unhealthy';
        if (data.reasons && data.reasons.length) {
            reasons.textContent = data.reasons.join(' • ');
            reasons.style.display = 'block';
        } else {
            reasons.style.display = 'none';
        }
    } else {
        banner.className = 'health-status-banner healthy';
        icon.innerHTML = '&#10003;';
        text.textContent = 'System healthy';
        reasons.style.display = 'none';
    }

    // Memory
    const mem = data.memory || {};
    const memPct = mem.percent || 0;
    document.getElementById('healthMemValue').textContent = memPct + '%';
    document.getElementById('healthMemValue').className = 'health-card-value' + getHealthClass(memPct);
    document.getElementById('healthMemDetail').textContent = (mem.used_mb ? Math.round(mem.used_mb / 1024 * 10) / 10 : '--') + ' / ' + (mem.total_mb ? Math.round(mem.total_mb / 1024 * 10) / 10 : '--') + ' GB';
    const memBar = document.getElementById('healthMemBar');
    memBar.style.width = memPct + '%';
    memBar.className = 'health-bar' + getHealthClass(memPct);

    // CPU
    const cpu = data.cpu || {};
    const cpuPct = cpu.percent || 0;
    document.getElementById('healthCpuValue').textContent = cpuPct + '%';
    document.getElementById('healthCpuValue').className = 'health-card-value' + getHealthClass(cpuPct);
    document.getElementById('healthCpuDetail').textContent = (data.load ? data.load.cpu_count : '--') + ' cores';
    const cpuBar = document.getElementById('healthCpuBar');
    cpuBar.style.width = cpuPct + '%';
    cpuBar.className = 'health-bar' + getHealthClass(cpuPct);

    // Disk
    const disk = data.disk || {};
    const diskPct = disk.percent || 0;
    document.getElementById('healthDiskValue').textContent = diskPct + '%';
    document.getElementById('healthDiskValue').className = 'health-card-value' + getHealthClass(diskPct);
    let diskDetail = (disk.used_gb || '--') + ' / ' + (disk.total_gb || '--') + ' GB';
    if (disk.free_gb !== undefined) {
        diskDetail += ' (' + disk.free_gb + ' GB available)';
    }
    document.getElementById('healthDiskDetail').textContent = diskDetail;
    const diskBar = document.getElementById('healthDiskBar');
    diskBar.style.width = diskPct + '%';
    diskBar.className = 'health-bar' + getHealthClass(diskPct);

    // Load
    const load = data.load || {};
    const loadPct = Math.min(100, (load.per_core_1min || 0) * 50);
    const loadVal = load['1min'] !== undefined ? load['1min'] : '--';
    document.getElementById('healthLoadValue').textContent = loadVal;
    document.getElementById('healthLoadValue').className = 'health-card-value' + (load.per_core_1min > 2 ? ' critical' : (load.per_core_1min > 1.5 ? ' warning' : ''));
    document.getElementById('healthLoadDetail').textContent = (load['1min'] || '--') + ' / ' + (load['5min'] || '--') + ' / ' + (load['15min'] || '--') + ' (1/5/15 min)';
    const loadBar = document.getElementById('healthLoadBar');
    loadBar.style.width = loadPct + '%';
    loadBar.className = 'health-bar' + (load.per_core_1min > 2 ? ' critical' : (load.per_core_1min > 1.5 ? ' warning' : ''));

    // Processes
    const procs = data.processes || {};
    const procsEl = document.getElementById('healthProcesses');
    if (procs.error) {
        procsEl.textContent = 'Error: ' + procs.error;
    } else {
        procsEl.innerHTML = `Kiro CLI sessions: <strong>${procs.kiro_cli_sessions || 0}</strong> &nbsp;|&nbsp; MCP servers: <strong>${procs.mcp_servers || 0}</strong> &nbsp;|&nbsp; Python total: <strong>${procs.python_total || 0}</strong>`;
    }

    // Errors
    const errors = data.errors || [];
    const errorsSection = document.getElementById('healthErrorsSection');
    const errorsEl = document.getElementById('healthErrors');
    if (errors.length) {
        errorsSection.style.display = '';
        errorsEl.textContent = errors.map(e => e.replace(/\\n/g, '\n').replace(/\\r/g, '\r')).join('\n\n');
    } else {
        errorsSection.style.display = 'none';
    }

    // Logs
    const logs = data.logs || {};
    const logsEl = document.getElementById('healthLogs');
    const flaskLogs = logs.flask || [];
    if (flaskLogs.length) {
        logsEl.textContent = flaskLogs.join('\n');
        logsEl.scrollTop = logsEl.scrollHeight;
    } else {
        logsEl.textContent = 'No recent logs';
    }

    // Timestamp
    document.getElementById('healthTimestamp').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
}

function getHealthClass(pct) {
    if (pct >= 80) return ' critical';
    if (pct >= 65) return ' warning';
    return '';
}

// Start health polling on page load (every 30 seconds for indicator, faster when modal open)
setTimeout(() => {
    fetchHealth();
    setInterval(() => {
        if (!healthModalOpen) fetchHealth();
    }, 30000);
}, 1000);