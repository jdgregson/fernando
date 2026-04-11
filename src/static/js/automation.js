// --- Automation Panel (unified subagents + workflows) ---
function openAutomationPanel() {
    document.getElementById('automationOverlay').classList.add('open');
    document.getElementById('automationPanel').classList.add('open');
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
    loadAutomationRules();
    loadSubagents();
    updateTriggerFields();
}

function closeAutomationPanel() {
    document.getElementById('automationOverlay').classList.remove('open');
    document.getElementById('automationPanel').classList.remove('open');
}

function switchAutoTab(tab, btn) {
    document.querySelectorAll('.auto-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.auto-tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('autoTab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
    if (tab === 'subagents') loadSubagents();
    if (tab === 'schedules') { emitWithCsrf('get_at_jobs'); emitWithCsrf('get_cron_jobs'); }
    if (tab === 'history') emitWithCsrf('automation_get_history');
    if (tab === 'policy') loadMetaPolicy();
}

// --- Rules ---
function loadAutomationRules() { emitWithCsrf('automation_list_rules'); }

socket.on('automation_rules', (data) => {
    const list = document.getElementById('automationRulesList');
    const rules = data.rules;
    if (!rules.length) { list.innerHTML = '<div class="sa-empty">No rules. Default: drop all inbound.</div>'; return; }
    list.innerHTML = rules.map(r => {
        const trigger = r.trigger || {};
        const badges = [];
        badges.push(`<span class="sa-badge ${r.enabled ? (trigger.type === 'inbound' ? 'running' : 'scheduled') : ''}">${trigger.type || '?'}</span>`);
        if (r.created_by === 'agent') badges.push('<span class="sa-badge" style="background:#1a1a2e;color:#9d7cd8">agent</span>');
        if (r.fire_once) badges.push('<span class="sa-badge scheduled">once</span>');
        const details = [];
        if (trigger.from) details.push('from: ' + escapeHtml(trigger.from));
        if (trigger.subject_contains) details.push('subj: ' + escapeHtml(trigger.subject_contains));
        if (trigger.cron) details.push('cron: ' + escapeHtml(trigger.cron));
        if (trigger.at) details.push('at: ' + escapeHtml(trigger.at));
        return `
        <div class="subagent-card">
            <div class="sa-header">
                <span class="sa-id">${escapeHtml(r.name || r.id)}</span>
                <span>${badges.join(' ')}</span>
            </div>
            <div class="sa-meta">${details.join(' · ') || 'any'}</div>
            ${r.task ? `<div class="sa-task">${DOMPurify.sanitize(marked.parse(r.task.substring(0, 200)))}</div>` : ''}
            ${r.expires_at ? `<div class="sa-meta">expires: ${new Date(r.expires_at).toLocaleString()}</div>` : ''}
            <div class="sa-actions">
                <button class="sa-btn" onclick="toggleAutomationRule('${escapeHtml(r.id)}', ${!r.enabled})">${r.enabled ? 'Disable' : 'Enable'}</button>
                <button class="sa-btn sa-btn-danger" onclick="deleteAutomationRule('${escapeHtml(r.id)}')">Delete</button>
            </div>
        </div>`;
    }).join('');
});

function toggleAutomationRule(id, enabled) {
    emitWithCsrf('automation_toggle_rule', { rule_id: id, enabled });
}
socket.on('automation_rule_updated', () => { loadAutomationRules(); });

async function deleteAutomationRule(id) {
    if (await showConfirm('Delete this automation rule?')) {
        emitWithCsrf('automation_delete_rule', { rule_id: id });
    }
}
socket.on('automation_rule_deleted', () => { loadAutomationRules(); });
socket.on('automation_error', (data) => { showAlert('Error: ' + data.error); });

// --- Create rule ---
function updateTriggerFields() {
    const type = document.getElementById('autoTriggerType').value;
    const c = document.getElementById('autoTriggerFields');
    if (type === 'inbound') {
        c.innerHTML = `
            <div style="margin-bottom:14px">
                <label class="sa-form-label">From (email or domain)</label>
                <input type="text" id="autoFrom" class="sa-input">
            </div>
            <div style="margin-bottom:14px">
                <label class="sa-form-label">Subject contains (optional)</label>
                <input type="text" id="autoSubject" class="sa-input">
            </div>
            <div style="margin-bottom:14px">
                <label class="sa-form-label">Action</label>
                <select id="autoAction" class="sa-input">
                    <option value="dispatch">Dispatch (full message)</option>
                    <option value="summary">Summary (metadata only)</option>
                    <option value="drop">Drop</option>
                </select>
            </div>
            <div style="display:flex;align-items:center;gap:8px;font-size:13px;color:#888">
                <input type="checkbox" id="autoFireOnce">
                <label for="autoFireOnce">Fire once</label>
            </div>`;
    } else if (type === 'schedule') {
        c.innerHTML = `
            <div style="margin-bottom:14px">
                <label class="sa-form-label">Task</label>
                <textarea id="autoTask" class="sa-input sa-textarea"></textarea>
            </div>
            <div style="margin-bottom:14px">
                <label class="sa-form-label">Schedule type</label>
                <select id="autoSchedType" class="sa-input" onchange="updateSchedInput()">
                    <option value="at">At specific time</option>
                    <option value="every">Recurring</option>
                </select>
            </div>
            <div id="autoSchedFields"></div>`;
        updateSchedInput();
    } else {
        c.innerHTML = `
            <div style="margin-bottom:14px">
                <label class="sa-form-label">Task</label>
                <textarea id="autoTask" class="sa-input sa-textarea"></textarea>
            </div>
            <div style="margin-bottom:14px">
                <label class="sa-form-label">Context file (optional)</label>
                <input type="text" id="autoContext" class="sa-input">
            </div>`;
    }
}

function updateSchedInput() {
    const type = document.getElementById('autoSchedType').value;
    const c = document.getElementById('autoSchedFields');
    if (type === 'at') {
        c.innerHTML = '<input type="time" id="autoSchedTime" class="sa-input" style="margin-top:8px">';
    } else {
        c.innerHTML = `<select id="autoSchedInterval" class="sa-input" style="margin-top:8px">
            <option value="minute">Every minute</option>
            <option value="5 minutes">Every 5 minutes</option>
            <option value="15 minutes">Every 15 minutes</option>
            <option value="30 minutes">Every 30 minutes</option>
            <option value="hour">Every hour</option>
            <option value="day">Every day</option>
            <option value="week">Every week</option>
        </select>`;
    }
}

function createAutomationRule() {
    const name = document.getElementById('autoRuleName').value.trim();
    const type = document.getElementById('autoTriggerType').value;
    const rule = { name: name || undefined, trigger: { type }, created_by: 'owner' };

    if (type === 'inbound') {
        const from = document.getElementById('autoFrom').value.trim();
        if (!from) { showAlert('From is required for inbound rules'); return; }
        rule.trigger.from = from;
        const subj = document.getElementById('autoSubject').value.trim();
        if (subj) rule.trigger.subject_contains = subj;
        rule.trigger.channel = 'email';
        rule.action = document.getElementById('autoAction').value;
        rule.fire_once = document.getElementById('autoFireOnce').checked;
    } else if (type === 'schedule') {
        rule.task = document.getElementById('autoTask').value.trim();
        if (!rule.task) { showAlert('Task is required'); return; }
        const schedType = document.getElementById('autoSchedType').value;
        if (schedType === 'at') {
            rule.trigger.at = document.getElementById('autoSchedTime').value;
        } else {
            rule.trigger.cron = document.getElementById('autoSchedInterval').value;
        }
    } else {
        rule.task = document.getElementById('autoTask').value.trim();
        if (!rule.task) { showAlert('Task is required'); return; }
        const ctx = document.getElementById('autoContext');
        if (ctx && ctx.value.trim()) rule.context_path = ctx.value.trim();
    }

    emitWithCsrf('automation_create_rule', { rule });
}

socket.on('automation_rule_created', () => {
    showAlert('Rule created');
    document.getElementById('autoRuleName').value = '';
    document.querySelector('.auto-tab').click();
    loadAutomationRules();
    loadSubagents();
});

// --- Subagents (running tasks) ---
function loadSubagents() { emitWithCsrf('list_subagents'); }

socket.on('subagents_list', (data) => {
    const list = document.getElementById('automationSubagentsList');
    const subagents = data.subagents;
    if (!subagents.length) { list.innerHTML = '<div class="sa-empty">No running subagents</div>'; return; }
    list.innerHTML = subagents.map(s => {
        const statusClass = s.status === 'running' ? 'running' : s.status === 'completed' ? 'completed' : s.status === 'failed' ? 'failed' : s.schedule ? 'scheduled' : '';
        return `
        <div class="subagent-card">
            <div class="sa-header">
                <span class="sa-id">${escapeHtml(s.task_id)}</span>
                <span class="sa-badge ${statusClass}">${escapeHtml(s.status)}${s.progress > 0 ? ' ' + s.progress + '%' : ''}</span>
            </div>
            <div class="sa-task">${DOMPurify.sanitize(marked.parse(s.task || ''))}</div>
            ${s.schedule ? `<div class="sa-schedule">⏱ ${escapeHtml(s.schedule)}</div>` : ''}
            ${s.current_step ? `<div class="sa-meta">${escapeHtml(s.current_step)}</div>` : ''}
            <div class="sa-actions">
                <button class="sa-btn" onclick="viewSubagent('${escapeHtml(s.task_id)}')">View</button>
                <button class="sa-btn" onclick="terminateSubagent('${escapeHtml(s.task_id)}')">Terminate</button>
                <button class="sa-btn sa-btn-danger" onclick="deleteSubagent('${escapeHtml(s.task_id)}')">Delete</button>
            </div>
        </div>`;
    }).join('');
});

function viewSubagent(taskId) { emitWithCsrf('get_subagent_status', { task_id: taskId }); }

socket.on('subagent_status', (data) => {
    const s = data;
    const statusClass = s.status === 'running' ? 'running' : s.status === 'completed' ? 'completed' : s.status === 'failed' ? 'failed' : 'scheduled';
    const sections = [
        `<div class="sa-detail-section"><div class="sa-detail-label">Task ID</div><div class="sa-detail-value"><code>${escapeHtml(s.task_id || '')}</code></div></div>`,
        `<div class="sa-detail-section"><div class="sa-detail-label">Status</div><div class="sa-detail-value"><span class="sa-badge ${statusClass}">${escapeHtml(s.status || '')}</span></div></div>`,
        `<div class="sa-detail-section"><div class="sa-detail-label">Task</div><div class="sa-detail-value">${DOMPurify.sanitize(marked.parse(s.task || ''))}</div></div>`,
        s.current_step ? `<div class="sa-detail-section"><div class="sa-detail-label">Current Step</div><div class="sa-detail-value">${escapeHtml(s.current_step)}</div></div>` : '',
        s.schedule ? `<div class="sa-detail-section"><div class="sa-detail-label">Schedule</div><div class="sa-detail-value">${escapeHtml(s.schedule)}</div></div>` : '',
        typeof s.progress !== 'undefined' ? `<div class="sa-detail-section"><div class="sa-detail-label">Progress</div><div class="sa-detail-value">${s.progress}%</div></div>` : '',
    ].filter(Boolean).join('');
    document.getElementById('alertMessage').innerHTML = sections;
    document.getElementById('alertButtons').innerHTML = '<button onclick="closeAlert()">Close</button>';
    document.getElementById('alertModal').classList.add('open');
});

async function terminateSubagent(taskId) {
    if (await showConfirm(`Terminate subagent ${taskId}?`)) emitWithCsrf('terminate_subagent', { task_id: taskId });
}
socket.on('subagent_terminated', () => { showAlert('Subagent terminated'); loadSubagents(); });

async function deleteSubagent(taskId) {
    if (await showConfirm(`Delete subagent ${taskId}? This will remove all data.`)) emitWithCsrf('delete_subagent', { task_id: taskId });
}
socket.on('subagent_deleted', () => { showAlert('Subagent deleted'); loadSubagents(); });

// --- Schedules ---
socket.on('at_jobs', (data) => {
    const list = document.getElementById('autoAtJobsList');
    if (data.jobs.length === 0) { list.innerHTML = '<p>No scheduled one-time jobs</p>'; return; }
    list.innerHTML = data.jobs.map(job => `
        <div class="subagent-card">
            <div class="sa-header">
                <span class="sa-id">Job ${escapeHtml(String(job.job_id))}</span>
                <span class="sa-status scheduled">${escapeHtml(job.scheduled_time)}</span>
            </div>
            <div class="sa-actions"><button class="sa-btn sa-btn-danger" onclick="removeAtJob('${escapeHtml(String(job.job_id))}')">Remove</button></div>
        </div>`).join('');
});

socket.on('cron_jobs', (data) => {
    const list = document.getElementById('autoCronJobsList');
    if (data.jobs.length === 0) { list.innerHTML = '<p>No recurring jobs</p>'; return; }
    list.innerHTML = data.jobs.map(job => `
        <div class="subagent-card">
            <div class="sa-header">
                <span class="sa-id">${escapeHtml(job.task_id || 'Unknown')}</span>
                <span class="sa-status scheduled">${escapeHtml(job.cron_time)}</span>
            </div>
            ${job.task_id ? `<div class="sa-actions"><button class="sa-btn sa-btn-danger" onclick="removeCronJob('${escapeHtml(job.task_id)}')">Remove</button></div>` : ''}
        </div>`).join('');
});

async function removeAtJob(jobId) {
    if (await showConfirm(`Remove scheduled job ${jobId}?`)) emitWithCsrf('remove_at_job', { job_id: jobId });
}
socket.on('at_job_removed', () => { showAlert('Job removed'); });

async function removeCronJob(taskId) {
    if (await showConfirm(`Remove recurring job for ${taskId}?`)) emitWithCsrf('remove_cron_job', { task_id: taskId });
}
socket.on('cron_job_removed', () => { showAlert('Job removed'); });

// --- History ---
socket.on('automation_history', (data) => {
    const list = document.getElementById('automationHistoryList');
    const history = data.history;
    if (!history.length) { list.innerHTML = '<div class="sa-empty">No history yet</div>'; return; }
    list.innerHTML = history.slice().reverse().map(h => {
        const badge = h.trigger_type === 'inbound' ? `<span class="sa-badge running">${h.action}</span>` : `<span class="sa-badge scheduled">${h.trigger_type}</span>`;
        return `
        <div style="padding:8px 0;border-bottom:1px solid #143151;font-size:12px">
            <div style="color:#555;font-size:11px">${new Date(h.timestamp).toLocaleString()}</div>
            <div style="color:#888;margin-top:2px">
                ${badge} ${escapeHtml(h.rule_name || h.rule_id)}
                ${h.message_from ? ' — ' + escapeHtml(h.message_from) : ''}
                ${h.message_subject ? ' — ' + escapeHtml(h.message_subject) : ''}
            </div>
            ${h.task_id ? `<div class="sa-meta">subagent: ${escapeHtml(h.task_id)}</div>` : ''}
        </div>`;
    }).join('');
});

// --- Meta Policy ---
function loadMetaPolicy() { emitWithCsrf('automation_get_meta_policy'); }

socket.on('automation_meta_policy', (data) => {
    const p = data.policy;
    document.getElementById('automationPolicyEditor').innerHTML = `
        <div style="margin-bottom:14px">
            <label class="sa-form-label">Allowed actions for agent rules</label>
            <input type="text" id="mpActions" class="sa-input" value="${(p.allowed_actions || []).join(', ')}">
        </div>
        <div style="margin-bottom:14px">
            <label class="sa-form-label">Allowed domains for agent rules</label>
            <input type="text" id="mpDomains" class="sa-input" value="${(p.allowed_domains || []).join(', ')}">
        </div>
        <div style="margin-bottom:14px">
            <label class="sa-form-label">Max TTL (hours) for agent rules</label>
            <input type="number" id="mpTtl" class="sa-input" value="${p.max_ttl_hours || 72}">
        </div>
        <div style="margin-bottom:14px">
            <label class="sa-form-label">Max active agent rules</label>
            <input type="number" id="mpMaxRules" class="sa-input" value="${p.max_active_agent_rules || 10}">
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:13px;color:#888">
            <input type="checkbox" id="mpFireOnce" ${p.require_fire_once_for_agent ? 'checked' : ''}>
            <label for="mpFireOnce">Require fire_once for agent rules</label>
        </div>
        <button class="sa-btn sa-btn-primary" onclick="saveMetaPolicy()">Save Policy</button>
    `;
});

function saveMetaPolicy() {
    emitWithCsrf('automation_update_meta_policy', { policy: {
        allowed_actions: document.getElementById('mpActions').value.split(',').map(s => s.trim()).filter(Boolean),
        allowed_domains: document.getElementById('mpDomains').value.split(',').map(s => s.trim()).filter(Boolean),
        max_ttl_hours: parseInt(document.getElementById('mpTtl').value) || 72,
        max_active_agent_rules: parseInt(document.getElementById('mpMaxRules').value) || 10,
        require_fire_once_for_agent: document.getElementById('mpFireOnce').checked,
    }});
}

socket.on('automation_meta_policy_saved', () => { showAlert('Meta-policy saved'); });
