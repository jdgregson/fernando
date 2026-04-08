// --- Subagent Panel ---
function openSubagentsModal() {
    document.getElementById('subagentsOverlay').classList.add('open');
    document.getElementById('subagentsPanel').classList.add('open');
    if (window.innerWidth <= 500) document.getElementById('sidebar').classList.remove('open');
    loadSubagents();
}

function closeSubagentsPanel() {
    document.getElementById('subagentsOverlay').classList.remove('open');
    document.getElementById('subagentsPanel').classList.remove('open');
}
function closeSubagentsModal() { closeSubagentsPanel(); }

function switchSaTab(tab, btn) {
    document.querySelectorAll('.sa-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.sa-tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('saTab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
    if (tab === 'schedules') {
        emitWithCsrf('get_at_jobs');
        emitWithCsrf('get_cron_jobs');
    }
}

function updateScheduleInput() {
    const type = document.getElementById('subagentScheduleType').value;
    const container = document.getElementById('scheduleInputContainer');
    if (type === 'at') {
        container.innerHTML = '<input type="time" id="scheduleTime" class="sa-input" style="margin-top:8px">';
    } else if (type === 'every') {
        container.innerHTML = `
            <select id="scheduleInterval" class="sa-input" style="margin-top:8px">
                <option value="minute">Every minute</option>
                <option value="5 minutes">Every 5 minutes</option>
                <option value="10 minutes">Every 10 minutes</option>
                <option value="15 minutes">Every 15 minutes</option>
                <option value="30 minutes">Every 30 minutes</option>
                <option value="hour">Every hour</option>
                <option value="day">Every day</option>
                <option value="week">Every week</option>
            </select>`;
    } else {
        container.innerHTML = '';
    }
}

async function loadSubagents() { emitWithCsrf('list_subagents'); }

socket.on('subagents_list', (data) => {
    const subagents = data.subagents;
    const list = document.getElementById('subagentsList');
    if (subagents.length === 0) { list.innerHTML = '<div class="sa-empty">No subagents yet</div>'; return; }
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

function createSubagent() {
    const taskId = document.getElementById('subagentTaskId').value;
    const task = document.getElementById('subagentTask').value;
    const contextPath = document.getElementById('subagentContextPath').value;
    const scheduleType = document.getElementById('subagentScheduleType').value;
    if (!taskId || !task) { showAlert('Task ID and Task are required'); return; }
    let schedule = null;
    if (scheduleType === 'at') {
        const time = document.getElementById('scheduleTime').value;
        if (time) schedule = `at ${time}`;
    } else if (scheduleType === 'every') {
        schedule = `every ${document.getElementById('scheduleInterval').value}`;
    }
    emitWithCsrf('create_subagent', { task_id: taskId, task: task, context_path: contextPath || null, schedule: schedule });
}

socket.on('subagent_created', () => {
    showAlert('Subagent created successfully');
    document.querySelector('.sa-tab').click();
    loadSubagents();
    document.getElementById('subagentTaskId').value = '';
    document.getElementById('subagentTask').value = '';
    document.getElementById('subagentContextPath').value = '';
    document.getElementById('subagentScheduleType').value = '';
    updateScheduleInput();
});

socket.on('subagent_error', (data) => { showAlert('Failed to create subagent: ' + data.error); });

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
        s.context_path ? `<div class="sa-detail-section"><div class="sa-detail-label">Context File</div><div class="sa-detail-value"><code>${escapeHtml(s.context_path)}</code></div></div>` : '',
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
    const list = document.getElementById('atJobsList');
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
    const list = document.getElementById('cronJobsList');
    if (data.jobs.length === 0) { list.innerHTML = '<p>No recurring jobs</p>'; return; }
    list.innerHTML = data.jobs.map(job => `
        <div class="subagent-card">
            <div class="sa-header">
                <span class="sa-id">${escapeHtml(job.task_id || 'Unknown')}</span>
                <span class="sa-status scheduled">${escapeHtml(job.cron_time)}</span>
            </div>
            <div class="sa-meta" style="overflow-wrap:break-word">${escapeHtml(job.command)}</div>
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
