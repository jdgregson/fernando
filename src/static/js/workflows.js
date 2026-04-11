// --- Workflow Panel ---
function openWorkflowsPanel() {
    document.getElementById('workflowsOverlay').classList.add('open');
    document.getElementById('workflowsPanel').classList.add('open');
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
    loadWorkflowRules();
}

function closeWorkflowsPanel() {
    document.getElementById('workflowsOverlay').classList.remove('open');
    document.getElementById('workflowsPanel').classList.remove('open');
}

function switchWfTab(tab, btn) {
    document.querySelectorAll('.wf-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.wf-tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('wfTab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
    if (tab === 'history') emitWithCsrf('workflow_get_history');
    if (tab === 'policy') loadMetaPolicy();
}

// --- Rules ---
function loadWorkflowRules() { emitWithCsrf('workflow_list_rules'); }

socket.on('workflow_rules', (data) => {
    const list = document.getElementById('workflowRulesList');
    const rules = data.rules;
    if (!rules.length) { list.innerHTML = '<div class="wf-empty">No rules yet. Default: drop all.</div>'; return; }
    list.innerHTML = rules.map(r => {
        const badges = [];
        badges.push(`<span class="wf-badge ${r.enabled ? r.action : 'disabled'}">${r.enabled ? r.action : 'disabled'}</span>`);
        if (r.created_by === 'agent') badges.push('<span class="wf-badge agent">agent</span>');
        if (r.fire_once) badges.push('<span class="wf-badge summary">once</span>');
        const m = r.match || {};
        const matchParts = [];
        if (m.channel) matchParts.push(m.channel);
        if (m.from) matchParts.push('from: ' + escapeHtml(m.from));
        if (m.subject_contains) matchParts.push('subj: ' + escapeHtml(m.subject_contains));
        return `
        <div class="wf-card">
            <div class="wf-header">
                <span class="wf-name">${escapeHtml(r.name || r.id)}</span>
                <span>${badges.join(' ')}</span>
            </div>
            <div class="wf-match">${matchParts.join(' · ') || 'any'}</div>
            ${r.expires_at ? `<div class="wf-meta">expires: ${new Date(r.expires_at).toLocaleString()}</div>` : ''}
            <div class="wf-actions">
                <button class="wf-btn" onclick="toggleWorkflowRule('${escapeHtml(r.id)}', ${!r.enabled})">${r.enabled ? 'Disable' : 'Enable'}</button>
                <button class="wf-btn wf-btn-danger" onclick="deleteWorkflowRule('${escapeHtml(r.id)}')">Delete</button>
            </div>
        </div>`;
    }).join('');
});

function createWorkflowRule() {
    const name = document.getElementById('wfRuleName').value.trim();
    const channel = document.getElementById('wfChannel').value;
    const from = document.getElementById('wfFrom').value.trim();
    const subject = document.getElementById('wfSubject').value.trim();
    const action = document.getElementById('wfAction').value;
    const fireOnce = document.getElementById('wfFireOnce').checked;
    if (!from) { showAlert('From field is required'); return; }
    const rule = {
        name: name || undefined,
        match: { channel },
        action,
        fire_once: fireOnce,
        created_by: 'owner',
    };
    if (from) rule.match.from = from;
    if (subject) rule.match.subject_contains = subject;
    emitWithCsrf('workflow_create_rule', { rule });
}

socket.on('workflow_rule_created', () => {
    showAlert('Rule created');
    document.getElementById('wfRuleName').value = '';
    document.getElementById('wfFrom').value = '';
    document.getElementById('wfSubject').value = '';
    document.getElementById('wfFireOnce').checked = false;
    document.querySelector('.wf-tab').click();
    loadWorkflowRules();
});

socket.on('workflow_error', (data) => { showAlert('Workflow error: ' + data.error); });

function toggleWorkflowRule(id, enabled) {
    emitWithCsrf('workflow_toggle_rule', { rule_id: id, enabled });
}
socket.on('workflow_rule_updated', () => { loadWorkflowRules(); });

async function deleteWorkflowRule(id) {
    if (await showConfirm('Delete this workflow rule?')) {
        emitWithCsrf('workflow_delete_rule', { rule_id: id });
    }
}
socket.on('workflow_rule_deleted', () => { loadWorkflowRules(); });

// --- History ---
socket.on('workflow_history', (data) => {
    const list = document.getElementById('workflowHistoryList');
    const history = data.history;
    if (!history.length) { list.innerHTML = '<div class="wf-empty">No matches yet</div>'; return; }
    list.innerHTML = history.slice().reverse().map(h => `
        <div class="wf-history-item">
            <div class="wf-h-time">${new Date(h.timestamp).toLocaleString()}</div>
            <div class="wf-h-detail">
                <span class="wf-badge ${h.action}">${h.action}</span>
                ${escapeHtml(h.message_from)} — ${escapeHtml(h.message_subject || '(no subject)')}
            </div>
            <div class="wf-meta">rule: ${escapeHtml(h.rule_name || h.rule_id)}</div>
        </div>
    `).join('');
});

// --- Meta Policy ---
function loadMetaPolicy() { emitWithCsrf('workflow_get_meta_policy'); }

socket.on('workflow_meta_policy', (data) => {
    const p = data.policy;
    const el = document.getElementById('workflowPolicyEditor');
    el.innerHTML = `
        <div class="wf-policy-field">
            <label>Allowed actions for agent rules</label>
            <input type="text" id="mpActions" class="wf-input" value="${(p.allowed_actions || []).join(', ')}">
        </div>
        <div class="wf-policy-field">
            <label>Allowed domains for agent rules</label>
            <input type="text" id="mpDomains" class="wf-input" value="${(p.allowed_domains || []).join(', ')}">
        </div>
        <div class="wf-policy-field">
            <label>Max TTL (hours) for agent rules</label>
            <input type="number" id="mpTtl" class="wf-input" value="${p.max_ttl_hours || 72}">
        </div>
        <div class="wf-policy-field">
            <label>Max active agent rules</label>
            <input type="number" id="mpMaxRules" class="wf-input" value="${p.max_active_agent_rules || 10}">
        </div>
        <div class="wf-checkbox-row">
            <input type="checkbox" id="mpFireOnce" ${p.require_fire_once_for_agent ? 'checked' : ''}>
            <label for="mpFireOnce">Require fire_once for agent rules</label>
        </div>
        <button class="wf-btn wf-btn-primary" onclick="saveMetaPolicy()">Save Policy</button>
    `;
});

function saveMetaPolicy() {
    const policy = {
        allowed_actions: document.getElementById('mpActions').value.split(',').map(s => s.trim()).filter(Boolean),
        allowed_domains: document.getElementById('mpDomains').value.split(',').map(s => s.trim()).filter(Boolean),
        max_ttl_hours: parseInt(document.getElementById('mpTtl').value) || 72,
        max_active_agent_rules: parseInt(document.getElementById('mpMaxRules').value) || 10,
        require_fire_once_for_agent: document.getElementById('mpFireOnce').checked,
    };
    emitWithCsrf('workflow_update_meta_policy', { policy });
}

socket.on('workflow_meta_policy_saved', () => { showAlert('Meta-policy saved'); });

// --- Inbound message notifications ---
