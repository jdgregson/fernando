const jobSessionMetadata = new Map();
let jobPickerGroup = null;

function rememberJob(job) {
    const previous = jobSessionMetadata.get(job.id);
    if (!previous || previous.session_revision <= job.session_revision) {
        jobSessionMetadata.set(job.id, Object.fromEntries(['id', 'name', 'state', 'paused', 'view_open', 'group_id', 'group_suspended', 'created', 'updated', 'session_revision'].map(key => [key, job[key]])));
    }
}

async function jobSessionRequest(path, data) {
    const options = {headers: {'X-API-Key': window.FERNANDO_API_KEY}};
    if (data !== undefined) {
        options.method = 'POST';
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(data);
    }
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Job operation failed');
    if (result.id) rememberJob(result);
    if (data !== undefined) emitWithCsrf('get_sessions');
    return result;
}

async function createJobSession() {
    jobPickerGroup = getNewSessionGroupId();
    closeNewSessionModal();
    const modal = document.getElementById('jobPickerModal');
    if (!modal.dataset.bound) {
        bindModalBackdrop(modal, closeJobPicker);
        modal.dataset.bound = 'true';
    }
    modal.classList.add('open');
    const select = document.getElementById('jobPickerSelect');
    select.replaceChildren(new Option('Loading...', ''));
    document.getElementById('jobPickerOpen').disabled = true;
    try {
        const result = await jobSessionRequest('/api/jobs');
        for (const job of result.jobs) rememberJob(job);
        select.replaceChildren(...result.jobs.map(job => new Option(job.name + ' · ' + (job.state === 'draft' ? 'draft' : job.paused ? 'paused' : job.state), job.id)));
        if (!result.jobs.length) select.append(new Option('No saved jobs', ''));
        document.getElementById('jobPickerOpen').disabled = !result.jobs.length;
    } catch (error) { showAlert(error.message); }
}

function closeJobPicker() {
    document.getElementById('jobPickerModal').classList.remove('open');
    jobPickerGroup = null;
}

async function createNewJob() {
    const groupId = jobPickerGroup;
    closeJobPicker();
    try {
        const job = await jobSessionRequest('/api/jobs/drafts', {group_id: groupId});
        await paneController.open('job:' + job.id);
    } catch (error) { showAlert(error.message); }
}

function openSelectedJob() {
    const id = document.getElementById('jobPickerSelect').value;
    if (!id) return;
    closeJobPicker();
    paneController.open('job:' + id);
}

async function jobSessionAction(id, action, values = {}) {
    return jobSessionRequest('/api/jobs/' + id + '/operator', {action, ...values});
}

paneController.register('job', {
    sidebarOrder: 2.6,
    selectInactiveOnly: true,
    matches: key => key.startsWith('job:'),
    urlFragment: '/jobs/',
    keyFromUrl(url) {
        const location = new URL(url, window.location.origin);
        return location.pathname.startsWith('/jobs/') ? 'job:' + decodeURIComponent(location.pathname.slice(6)) : null;
    },
    sessions(data) {
        return (data.job_sessions || []).map(job => {
            rememberJob(job);
            return {...jobSessionMetadata.get(job.id), key: 'job:' + job.id};
        });
    },
    beforeLeave: async ({frame}) => { if (frame?.contentWindow.saveJobDraft) await frame.contentWindow.saveJobDraft(); },
    mount({key, browser}) {
        if (browser.querySelector('iframe')?.dataset.jobKey === key) return;
        const query = new URLSearchParams({api_key: window.FERNANDO_API_KEY});
        const frame = document.createElement('iframe');
        frame.dataset.jobKey = key;
        frame.dataset.opening = 'true';
        frame.src = '/jobs/' + encodeURIComponent(key.slice(4)) + '?' + query;
        frame.style.cssText = 'width:100%;height:100%;border:none';
        browser.replaceChildren(frame);
    },
    afterOpen({key, browser, pane}) {
        if (key === 'job:new') return;
        const frame = browser.querySelector('iframe');
        frame.jobOpening = jobSessionAction(key.slice(4), 'open');
        frame.jobOpening.then(() => {
            frame.dataset.opening = 'false';
            if (browser.querySelector('iframe') === frame && jobSessionMetadata.get(key.slice(4))?.view_open === 0) paneController.showTerminal(pane, {emptyBrowser: true, fit: true});
            paneController.broadcastState();
            syncUrlParams();
        }).catch(error => { showAlert(error.message); });
    },
    context(key) {
        const job = jobSessionMetadata.get(key.slice(4));
        return {type: 'job', description: job ? job.name + ' (' + (job.state === 'draft' ? 'draft' : job.paused ? 'paused' : job.state) + ')' : 'Job ' + key.slice(4)};
    },
    describeSession: job => 'job:' + job.id + ' (' + job.name + ', ' + (job.paused ? 'paused' : job.state) + ')',
    closeLabel: 'Close',
    dismiss: null,
    async close(key) {
        try {
            const views = [];
            for (const pane of paneController.panes.keys()) {
                const frame = paneController.elements(pane).browser.querySelector('iframe');
                if (frame?.dataset.jobKey !== key) continue;
                views.push({pane, frame, request: paneController.get(pane).openRequest});
            }
            for (const {frame} of views) {
                if (frame.jobOpening) await frame.jobOpening;
                if (frame.contentWindow.saveJobDraft) await frame.contentWindow.saveJobDraft();
            }
            if (key !== 'job:new') await jobSessionAction(key.slice(4), 'close');
            for (const view of views) {
                if (paneController.elements(view.pane).browser.querySelector('iframe') === view.frame && paneController.get(view.pane).openRequest === view.request) paneController.showTerminal(view.pane, {emptyBrowser: true, fit: true});
            }
            syncUrlParams();
        } catch (error) { showAlert(error.message); }
    },
    sidebar: job => ({
        title: job.state === 'draft' ? 'draft' : job.paused ? 'paused' : job.state,
        icon: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" style="vertical-align:-1px;margin-right:4px"><rect x="3" y="2" width="10" height="13" rx="1"/><path d="M5 6h6M5 9h6M5 12h4"/></svg>',
        closeLabel: 'Close',
        rename: name => jobSessionAction(job.id, 'rename', {name}).catch(error => showAlert(error.message)),
    }),
});

socket.on('job_changed', data => {
    for (const job of data.jobs || []) {
        rememberJob(job);
    }
    for (const pane of paneController.panes.keys()) {
        const key = paneController.get(pane).contentKey;
        if (!key?.startsWith('job:') || key === 'job:new') continue;
        const job = jobSessionMetadata.get(key.slice(4));
        const frame = paneController.elements(pane).browser.querySelector('iframe');
        if (job && !job.view_open && frame?.dataset.opening !== 'true') paneController.showTerminal(pane, {emptyBrowser: true, fit: true});
        else if (job) paneController.post(pane, 'job-update', {job});
    }
    paneController.broadcastState();
    syncUrlParams();
});

window.addEventListener('message', event => {
    const pane = [...paneController.panes.keys()].find(id => paneController.isEmbedded(id, 'job') && paneController.elements(id).browser.querySelector('iframe')?.contentWindow === event.source);
    if (!pane || event.origin !== window.location.origin) return;
    if (event.data?.type === 'job-created' && /^[a-f0-9]{32}$/.test(event.data.id)) {
        const state = paneController.get(pane);
        state.contentKey = 'job:' + event.data.id;
        const frame = paneController.elements(pane).browser.querySelector('iframe');
        frame.dataset.jobKey = state.contentKey;
        frame.dataset.opening = 'false';
        if (event.data.job) rememberJob(event.data.job);
        emitWithCsrf('get_sessions');
        syncUrlParams();
        paneController.broadcastState();
    }
    if (event.data?.type === 'job-open-chat' && /^[a-f0-9]{8}$/.test(event.data.id)) {
        setActiveTerminal(pane, false);
        paneController.open('chat:' + event.data.id);
    }
});
