const config = window.jobConfig;
const errorBox = document.getElementById('jobError');
let jobId = config.id;
let lastUpdated = null;
let sourceId = null;
let currentJob = config.job;
let draftRevision = currentJob?.draft_revision || 0;
let draftDirty = false;
let draftReady = false;
let draftSaving = null;
let draftTimer = null;
let refreshing = null;
let documentUpload = null;
const modelLoads = new Map();

function draftValues() {
    const model = role => ({backend: document.getElementById(role + 'Provider').value, model: document.getElementById(role + 'Model').value});
    return {name: document.getElementById('jobName').value, scope: document.getElementById('jobScope').value, source_id: sourceId, source_name: document.getElementById('jobDocumentName').textContent, worker: model('worker'), reviewer: model('reviewer'), max_workers: Number(document.getElementById('jobMaxWorkers').value), reuse_workers: document.getElementById('jobReuseWorkers').checked};
}

function sameDraft(left, right) {
    return ['name', 'scope', 'source_id', 'source_name'].every(key => left[key] === right[key]) &&
        (left.max_workers ?? 5) === (right.max_workers ?? 5) && !!left.reuse_workers === !!right.reuse_workers &&
        ['worker', 'reviewer'].every(role => left[role].backend === right[role].backend && left[role].model === right[role].model);
}

function queueDraftSave() {
    if (!draftReady || currentJob?.state !== 'draft') return;
    draftDirty = true;
    sessionStorage.setItem('job-draft:' + jobId, JSON.stringify({revision: draftRevision, values: draftValues()}));
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => saveDraft().catch(error => { errorBox.textContent = error.message; }), 600);
}

async function saveDraft(submit = false) {
    clearTimeout(draftTimer);
    if (currentJob?.state !== 'draft') return currentJob;
    while (draftSaving) await draftSaving;
    if (currentJob?.state !== 'draft') return currentJob;
    const saving = (async () => {
        if (documentUpload) await documentUpload;
        await Promise.all(modelLoads.values());
        if (!draftDirty && !submit) return currentJob;
        if (jobId === 'new') {
            const job = await request('/api/jobs/drafts', {group_id: new URLSearchParams(location.search).get('group_id')});
            const pending = sessionStorage.getItem('job-draft:new');
            jobId = job.id;
            currentJob = job;
            draftRevision = job.draft_revision;
            if (pending) {
                sessionStorage.setItem('job-draft:' + jobId, pending);
                sessionStorage.removeItem('job-draft:new');
            }
            history.replaceState(null, '', '/jobs/' + jobId + '?api_key=' + encodeURIComponent(config.apiKey));
            window.parent.postMessage({type: 'job-created', id: jobId, job}, window.location.origin);
        }
        const values = draftValues();
        const job = await request('/api/jobs/' + jobId + '/draft', {draft: values, revision: draftRevision, submit});
        currentJob = job;
        draftRevision = job.draft_revision;
        draftDirty = !sameDraft(values, draftValues());
        if (!draftDirty || submit) sessionStorage.removeItem('job-draft:' + jobId);
        else queueDraftSave();
        errorBox.textContent = '';
        document.getElementById('jobTitle').textContent = job.name;
        return job;
    })();
    draftSaving = saving;
    try { return await saving; }
    finally { draftSaving = null; }
}

window.saveJobDraft = () => saveDraft();

async function request(path, data) {
    const options = {headers: {'X-API-Key': config.apiKey}};
    if (data !== undefined) {
        options.method = 'POST';
        if (data instanceof FormData) options.body = data;
        else {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(data);
        }
    }
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Job request failed');
    return result;
}

function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = content;
    return element;
}

function button(label, action) {
    const element = node('button', 'sa-btn', label);
    element.type = 'button';
    element.onclick = async () => {
        element.disabled = true;
        try { await action(); errorBox.textContent = ''; }
        catch (error) { errorBox.textContent = error.message; }
        finally { element.disabled = false; }
    };
    return element;
}

function field(parent, label, value) {
    const section = node('section', 'sa-detail-section');
    section.append(node('h3', 'sa-detail-label', label), node('div', 'sa-task', value));
    parent.append(section);
}

function details(label, key) {
    const element = node('details', 'subagent-card');
    element.dataset.key = key;
    element.append(node('summary', 'sa-id sa-meta', label));
    return element;
}

async function operator(action, data = {}) {
    const job = await request('/api/jobs/' + jobId + '/operator', {action, ...data});
    if (job.state === 'draft') await loadDraft(job);
    else render(job);
}

function answerForm(parent, label, submit) {
    const form = node('form', 'sa-detail-section');
    const input = node('textarea', 'sa-input sa-textarea');
    input.required = true;
    input.dataset.draft = parent.dataset.key + ':' + label;
    input.value = sessionStorage.getItem('job-answer:' + jobId + ':' + input.dataset.draft) || '';
    input.addEventListener('input', () => sessionStorage.setItem('job-answer:' + jobId + ':' + input.dataset.draft, input.value));
    input.setAttribute('aria-label', label);
    const send = button(label, async () => { if (form.reportValidity()) await submit(input.value); });
    form.append(input, send);
    form.onsubmit = event => event.preventDefault();
    parent.append(form);
}

function evidenceButton(parent, ref) {
    const open = button('Evidence ' + ref.slice(0, 8), async () => {
        const evidence = await request('/api/jobs/' + jobId + '/evidence/' + ref);
        const raw = details('Original tool event · ' + evidence.sha256, 'evidence-' + ref);
        field(raw, 'Recorded event', JSON.stringify(evidence.event, null, 2));
        raw.open = true;
        parent.append(raw);
    });
    parent.append(open);
}

function render(job) {
    currentJob = job;
    lastUpdated = job.updated;
    document.getElementById('jobTitle').textContent = job.name;
    document.getElementById('jobState').textContent = job.state === 'draft' ? 'draft' : job.group_suspended ? 'waiting for group restore' : job.paused ? 'paused' : job.state.replaceAll('_', ' ');
    document.getElementById('jobCreate').hidden = job.state !== 'draft';
    const view = document.getElementById('jobView');
    if (job.state === 'draft') {
        view.replaceChildren();
        return;
    }
    const expanded = new Set([...view.querySelectorAll('details[open]')].map(element => element.dataset.key));
    const drafts = new Map([...view.querySelectorAll('textarea[data-draft]')].map(element => [element.dataset.draft, element.value]));
    const fragment = document.createDocumentFragment();
    const progress = node('section', 'subagent-card');
    const count = job.requirements.filter(item => ['accepted', 'waived'].includes(item.state)).length;
    field(progress, 'Progress', job.plan_accepted ? `${count} / ${job.requirements.length} requirements accepted or waived` : 'Decomposition and independent scope review');
    field(progress, 'Models', `Worker: ${job.worker.backend} / ${job.worker.model}\nReviewer: ${job.reviewer.backend} / ${job.reviewer.model}`);
    field(progress, 'Execution', `Max workers: ${job.max_workers}\nWorker reuse: ${job.reuse_workers ? 'up to 3 attempts per test' : 'new worker per attempt'}`);
    if (!['draft', 'complete', 'cancelled'].includes(job.state)) {
        progress.append(button(job.paused ? 'Resume' : 'Pause dispatch', () => operator(job.paused ? 'resume' : 'pause')));
    }
    fragment.append(progress);
    const scope = details('Original scope', 'scope');
    field(scope, 'Scope SHA-256', job.scope_sha256);
    field(scope, 'Scope', job.scope);
    if (job.uploaded_source) {
        field(scope, 'Uploaded source', job.uploaded_source.filename + '\nSHA-256: ' + job.uploaded_source.sha256);
        field(scope, 'Original extracted document text', job.uploaded_source.extracted);
        scope.append(button('Download original document', () => {
            const link = document.createElement('a');
            link.href = '/api/jobs/' + jobId + '/source?api_key=' + encodeURIComponent(config.apiKey);
            link.download = job.uploaded_source.filename;
            link.click();
        }));
    }
    fragment.append(scope);
    for (const item of job.operator_requests) {
        const card = details(item.answer ? 'Resolved request' : 'Needs your assistance', item.id);
        if (!item.answer) card.open = true;
        field(card, 'Request', item.request);
        if (item.requirement_id) field(card, 'Requirement', job.requirements.find(req => req.id === item.requirement_id)?.spec.key || item.requirement_id);
        if (item.answer) field(card, 'Answer', item.answer);
        else answerForm(card, 'Answer and retry', answer => operator('answer', {request_id: item.id, answer}));
        fragment.append(card);
    }
    if (!job.plan_accepted && job.plan) {
        const plan = details('Proposed requirements', 'plan');
        for (const spec of job.plan) field(plan, spec.key, JSON.stringify(spec, null, 2));
        fragment.append(plan);
    }
    for (const requirement of job.requirements) {
        const card = details(requirement.spec.key + ' · ' + requirement.state.replaceAll('_', ' '), requirement.id);
        for (const [key, label] of Object.entries({work: 'Work', source: 'Original obligation', acceptance: 'Acceptance criteria', preconditions: 'Preconditions', access: 'Required access', dependencies: 'Dependencies', evidence: 'Required evidence', controls: 'Controls'})) {
            field(card, label, Array.isArray(requirement.spec[key]) ? requirement.spec[key].join(', ') || 'None' : requirement.spec[key]);
        }
        for (const attempt of job.attempts.filter(item => item.requirement_id === requirement.id)) {
            const result = details('Attempt · ' + attempt.state.replaceAll('_', ' '), attempt.id);
            field(result, 'Conclusion', attempt.conclusion);
            field(result, 'Outcome', attempt.outcome);
            field(result, 'Limitations', attempt.limitations);
            field(result, 'Versions', attempt.versions.join('\n'));
            for (const ref of attempt.evidence) evidenceButton(result, ref);
            for (const review of job.reviews.filter(item => item.attempt_id === attempt.id)) {
                field(result, 'Review · ' + review.verdict, review.reasons);
                for (const ref of review.evidence) evidenceButton(result, ref);
            }
            card.append(result);
        }
        if (requirement.waiver) field(card, 'User waiver', requirement.waiver);
        else if (!['accepted', 'waived'].includes(requirement.state)) {
            const waiver = details('Waive requirement', 'waive-' + requirement.id);
            answerForm(waiver, 'Confirm waiver', reason => operator('waive', {requirement_id: requirement.id, reason}));
            card.append(waiver);
        }
        fragment.append(card);
    }
    const agents = details('Assignments and attempts', 'assignments');
    for (const assignment of job.assignments) {
        const row = node('div', 'sa-detail-section');
        field(row, assignment.role + ' · ' + assignment.state, assignment.requirement_id || 'Scope decomposition');
        if (assignment.session_id) row.append(button('Open agent', () => window.parent.postMessage({type: 'job-open-chat', id: assignment.session_id}, window.location.origin)));
        agents.append(row);
    }
    fragment.append(agents);
    const reviews = details('Scope review decisions', 'reviews');
    for (const review of job.reviews.filter(item => !item.attempt_id)) field(reviews, 'Revision ' + review.revision + ' · ' + review.verdict, review.reasons);
    fragment.append(reviews);
    const activity = details('Activity', 'activity');
    for (const event of job.job_events) field(activity, new Date(event.created * 1000).toLocaleString(undefined, {hour12: false}) + ' · ' + event.kind, event.detail);
    fragment.append(activity);
    view.replaceChildren(fragment);
    for (const element of view.querySelectorAll('details')) if (expanded.has(element.dataset.key)) element.open = true;
    for (const element of view.querySelectorAll('textarea[data-draft]')) if (drafts.has(element.dataset.draft)) element.value = drafts.get(element.dataset.draft);
}

async function models(role) {
    const provider = document.getElementById(role + 'Provider').value;
    const select = document.getElementById(role + 'Model');
    select.replaceChildren();
    const loading = request('/api/providers/' + encodeURIComponent(provider) + '/models');
    modelLoads.set(role, loading);
    let result;
    try { result = await loading; }
    finally { if (modelLoads.get(role) === loading) modelLoads.delete(role); }
    if (provider !== document.getElementById(role + 'Provider').value) return;
    select.replaceChildren(...result.models.map(model => new Option(model.name, model.id)));
    const saved = currentJob?.draft?.[role];
    const preferred = saved?.backend === provider ? saved.model : config.providers.find(item => item.id === provider).model;
    if ([...select.options].some(option => option.value === preferred)) select.value = preferred;
}

document.getElementById('jobChooseDocument').onclick = () => {
    document.getElementById('jobDocument').value = '';
    document.getElementById('jobDocument').click();
};

document.getElementById('jobDocument').onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('document', file);
    documentUpload = request('/api/jobs/document', form);
    try {
        const result = await documentUpload;
        sourceId = result.source_id;
        document.getElementById('jobDocumentName').textContent = result.name;
        document.getElementById('jobScope').value = result.scope;
        if (!document.getElementById('jobName').value) document.getElementById('jobName').value = file.name.slice(0, 120);
        queueDraftSave();
        errorBox.textContent = '';
    } catch (error) { errorBox.textContent = error.message; }
    finally { documentUpload = null; }
};

document.getElementById('jobCreate').onsubmit = async event => {
    event.preventDefault();
    const submit = document.getElementById('jobCreateButton');
    submit.disabled = true;
    try {
        const job = await saveDraft(true);
        document.getElementById('jobCreate').hidden = true;
        history.replaceState(null, '', '/jobs/' + jobId + location.search);
        window.parent.postMessage({type: 'job-created', id: jobId, job}, window.location.origin);
        render(job);
    } catch (error) { errorBox.textContent = error.message; }
    finally { submit.disabled = false; }
};

async function refresh() {
    if (jobId === 'new') return;
    if (refreshing) return refreshing;
    refreshing = (async () => {
        const job = await request('/api/jobs/' + jobId);
        if (job.state === 'draft' && !draftDirty && !draftSaving && (!draftReady || job.draft_revision !== draftRevision)) await loadDraft(job);
        if (lastUpdated !== job.updated && !document.querySelector('#jobView textarea:focus') && !draftSaving) render(job);
    })();
    try { return await refreshing; }
    finally { refreshing = null; }
}

async function loadDraft(job) {
    currentJob = job;
    draftReady = false;
    draftRevision = job.draft_revision;
    const pending = JSON.parse(sessionStorage.getItem('job-draft:' + jobId) || 'null');
    const values = pending?.values || job.draft;
    document.getElementById('jobName').value = values.name;
    document.getElementById('jobScope').value = values.scope;
    document.getElementById('jobMaxWorkers').value = values.max_workers ?? 5;
    document.getElementById('jobReuseWorkers').checked = !!values.reuse_workers;
    sourceId = values.source_id;
    document.getElementById('jobDocumentName').textContent = values.source_name || '';
    try {
        for (const role of ['worker', 'reviewer']) {
            document.getElementById(role + 'Provider').value = values[role].backend;
            await models(role);
            document.getElementById(role + 'Model').value = values[role].model;
        }
    } finally { draftReady = true; }
    if (pending && !sameDraft(pending.values, job.draft)) {
        draftRevision = pending.revision;
        queueDraftSave();
    } else if (pending) sessionStorage.removeItem('job-draft:' + jobId);
    render(job);
}

for (const role of ['worker', 'reviewer']) {
    document.getElementById(role + 'Provider').onchange = () => {
        draftDirty = true;
        models(role).then(queueDraftSave).catch(error => { errorBox.textContent = error.message; });
    };
}
document.getElementById('jobCreate').addEventListener('input', event => {
    if (!event.target.id.endsWith('Provider') && event.target.id !== 'jobDocument') queueDraftSave();
});
window.FernandoPane.on('job-update', () => refresh().catch(error => { errorBox.textContent = error.message; }));

(async () => {
    if (jobId === 'new') {
        const provider = config.providers.find(item => item.enabled);
        if (!provider) throw new Error('Enable an agent provider before creating a Job');
        const model = {backend: provider.id, model: provider.model};
        currentJob = {id: 'new', name: 'New Job', state: 'draft', updated: 0, draft_revision: 0,
            draft: {name: '', scope: '', source_id: null, source_name: '', worker: model, reviewer: model, max_workers: 5, reuse_workers: false}};
    }
    if (currentJob?.state === 'draft') await loadDraft(currentJob);
    else if (currentJob) render(currentJob);
    else await refresh();
    setInterval(() => refresh().catch(error => { errorBox.textContent = error.message; }), 3000);
})().catch(error => { errorBox.textContent = error.message; });
