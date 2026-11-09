const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('src/templates/chat.html', 'utf8');
function extract(name) {
    const start = source.indexOf('    function ' + name + '(');
    const end = source.indexOf('\n    }', start) + 6;
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end);
}
function element() {
    return {
        dataset: {}, children: [], isConnected: true,
        appendChild(child) { this.children.push(child); child.parent = this; },
        remove() { this.isConnected = false; this.parent.children = this.parent.children.filter(child => child !== this); },
        setAttribute(key, value) { this[key] = value; },
        querySelectorAll(selector) {
            const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
            if (selector === '*') return all;
            if (selector === 'button') return all.filter(child => child.tag === 'button');
            if (selector.startsWith('.')) return all.filter(child => child.className === selector.slice(1));
            const field = selector === '[data-resume-id]' ? 'resumeId' : 'resumeModal';
            return all.filter(child => child.dataset[field]);
        },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    };
}
const body = element();
const messages = element();
body.appendChild(messages);
const requests = [];
const sandbox = vm.createContext({
    document: {
        body, createElement(tag) { return Object.assign(element(), {tag}); },
        querySelectorAll(selector) { return body.querySelectorAll(selector); },
        getElementById() { return messages; }, addEventListener() {},
    },
    bindModalBackdrop() {}, scrollToBottom() {}, sessionId: 'aaaaaaaa', csrfToken: 'csrf',
    socket: {timeout() { return this; }, emit(...args) { requests.push(args); }},
});
vm.runInContext(['showContinuationModal', 'resumeStatus', 'resumeTime', 'resumeDetails', 'showResumeModal', 'addScheduledResume'].map(extract).join('\n'), sandbox);
const row = {id: 'schedule1', session_id: 'aaaaaaaa', status: 'scheduled', resume_at: 1800000000,
    reason: '<img src=x onerror=alert(1)>', continuation: 'Inspect the job', error: null};
sandbox.addScheduledResume(row);
assert.equal(messages.children.length, 1);
const message = messages.children[0];
assert.equal(message.className, 'msg system continuation scheduled-resume');
assert.equal(message.innerHTML, undefined);
assert.ok(message.textContent.includes(row.reason));
message.onclick();
let overlay = body.children.at(-1);
assert.equal(overlay.children[0].className, 'continuation-modal');
assert.ok(overlay.querySelector('.continuation-modal-body').textContent.includes('Inspect the job'));
let buttons = overlay.querySelectorAll('button');
assert.deepEqual(buttons.map(button => button.textContent), ['Resume now', 'Cancel']);
buttons[1].onclick();
assert.equal(requests[0][0], 'acp_manage_resume');
assert.equal(requests[0][1].csrf_token, 'csrf');
assert.equal(requests[0][1].action, 'cancel');
assert.equal(requests[0][1].session_id, row.session_id);
requests[0][2](null, {schedule: {...row, status: 'canceled'}});
assert.equal(messages.children.length, 1);
assert.ok(message.textContent.startsWith('Resume canceled'));
assert.equal(body.children.at(-1).querySelectorAll('button').length, 0);
body.children.at(-1).remove();
sandbox.addScheduledResume({...row, status: 'queued'});
message.onclick();
overlay = body.children.at(-1);
overlay.querySelectorAll('button')[0].onclick();
assert.equal(requests[1][1].action, 'resume_now');
requests[1][2](new Error('timeout'));
assert.ok(overlay.querySelector('.continuation-modal-body').textContent.includes('No confirmation received'));
assert.equal(overlay.querySelectorAll('button')[0].disabled, false);
sandbox.addScheduledResume({...row, status: 'failed', error: 'Could not wake'});
assert.equal(overlay.querySelectorAll('button').length, 0);
assert.ok(overlay.querySelector('.continuation-modal-body').textContent.includes('Could not wake'));
assert.equal(messages.children.length, 1);
overlay.remove();
sandbox.addScheduledResume({...row, session_id: 'other-chat'});
message.onclick();
assert.equal(body.children.at(-1).querySelectorAll('button').length, 0);
console.log('Scheduled resume rows, modal controls, live updates, safe text, and failure states passed');
