class ChatSearchNavigation {
    constructor({messages, pauseFollowing, failed, search, cancel}) {
        this.messages = messages;
        this.pauseFollowing = pauseFollowing;
        this.failed = failed;
        this.search = search;
        this.cancel = cancel;
        this.requestId = 0;
        this.matches = [];
        this.related = [];
        this.selected = 0;
        this.searching = false;
        this.query = '';
        this.requested = false;
        this.navigation = document.createElement('nav');
        this.navigation.className = 'chat-search-navigation';
        this.navigation.hidden = true;
        this.navigation.setAttribute('aria-label', 'Search matches');
        this.previousButton = document.createElement('button');
        this.previousButton.textContent = 'Previous';
        this.previousButton.onclick = () => this.move(-1);
        this.counter = document.createElement('span');
        this.counter.setAttribute('role', 'status');
        this.nextButton = document.createElement('button');
        this.nextButton.textContent = 'Next';
        this.nextButton.onclick = () => this.move(1);
        this.closeButton = document.createElement('button');
        this.closeButton.textContent = 'Close';
        this.closeButton.onclick = () => {
            this.navigation.hidden = true;
            this.query = '';
            this.requestId++;
            this.cancel?.({request_id: this.requestId});
            this.messages.querySelectorAll('.search-target').forEach(node => node.classList.remove('search-target'));
            if (window.CSS?.highlights) CSS.highlights.delete('chat-search-match');
            history.replaceState(null, '', window.location.pathname + window.location.search);
        };
        this.navigation.append(this.previousButton, this.counter, this.nextButton, this.closeButton);
        const viewport = messages.closest('.messages-viewport') || messages;
        viewport.before(this.navigation);
        this.entries = [];
        this.turn = -1;
        this.turns = new Map();
        this.ready = false;
        this.pending = null;
        window.addEventListener('hashchange', () => this.request());
        this.request();
    }

    request() {
        const params = new URLSearchParams(window.location.hash.slice(1));
        this.query = params.get('search-query') || '';
        this.scope = params.get('search-scope');
        this.matches = [];
        this.related = [];
        this.selected = 0;
        this.requested = false;
        this.requestId++;
        const event = params.get('search-event');
        const turn = params.get('search-turn');
        const role = params.get('search-role');
        if (event !== null && /^\d+$/.test(event)) {
            this.pending = {event: Number(event), text: params.get('search-text') || '', source: params.get('search-source') || ''};
        } else if (turn !== null && /^\d+$/.test(turn) && ['user', 'assistant'].includes(role)) {
            this.pending = {turn: Number(turn), role, text: '', source: ''};
        } else {
            return;
        }
        if (this.ready) {
            this.jump();
            this.loadMatches();
        }
    }

    reset() {
        this.entries = [];
        this.turns.clear();
        this.turn = -1;
        this.ready = false;
    }

    register(event, element, start, end) {
        const update = event.params?.update;
        if (['user_prompt', 'continuation'].includes(event.type)) {
            this.turn++;
            if (element) this.turns.set(this.turn + ':user', element);
        }
        if (update?.sessionUpdate === 'agent_message_chunk' && element && !this.turns.has(this.turn + ':assistant')) {
            this.turns.set(this.turn + ':assistant', element);
        }
        if (!element || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return;
        const previous = this.entries.at(-1);
        if (previous?.element === element && previous.end + 1 === start) previous.end = end;
        else this.entries.push({start, end, element});
    }

    loaded() {
        this.ready = true;
        if (this.pending) this.jump();
        this.loadMatches();
    }

    loadMatches() {
        if (!this.query || this.requested || !['active', 'archived'].includes(this.scope)) return;
        this.requested = true;
        this.searching = true;
        this.navigation.hidden = false;
        this.navigation.title = this.query;
        this.updateCounter();
        this.search({query: this.query, scope: this.scope, request_id: this.requestId});
    }

    receive(data) {
        if (data.request_id !== this.requestId || data.query !== this.query || data.scope !== this.scope) return;
        for (const hit of data.sessions || []) {
            let target;
            if (Number.isSafeInteger(hit.event_index)) target = {event: hit.event_index, source: hit.source, text: hit.match_text, occurrence: hit.match_occurrence || 0};
            else if (Number.isSafeInteger(hit.turn_index) && ['user', 'assistant'].includes(hit.role)) target = {turn: hit.turn_index, role: hit.role, text: '', source: ''};
            else continue;
            const collection = target.event !== undefined ? this.matches : this.related;
            if (!collection.some(match => JSON.stringify(match) === JSON.stringify(target))) collection.push(target);
        }
        this.matches.sort((a, b) => a.event - b.event || a.occurrence - b.occurrence);
        if (data.state === 'complete' || data.state === 'error') {
            this.searching = false;
            if (!this.matches.length) this.matches = this.related;
        }
        const current = this.currentTarget;
        this.selected = this.matches.findIndex(match => current && (match.event !== undefined
            ? match.event === current.event && match.source === current.source && match.occurrence === (current.occurrence || 0)
            : match.turn === current.turn && match.role === current.role));
        this.updateCounter();
        if (data.error) this.counter.textContent = data.error;
    }

    updateCounter() {
        this.counter.textContent = (this.matches.length ? (this.selected >= 0 ? this.selected + 1 : '—') + ' of ' + this.matches.length : '0 matches') + (this.searching ? ' · searching' : '');
        this.previousButton.disabled = this.selected <= 0;
        this.nextButton.disabled = this.selected + 1 >= this.matches.length;
    }

    move(direction) {
        const index = this.selected + direction;
        if (index < 0 || index >= this.matches.length) return;
        this.selected = index;
        this.pending = this.matches[index];
        this.jump();
        this.updateCounter();
    }

    jump() {
        const target = this.pending;
        this.pending = null;
        this.currentTarget = target;
        const element = target.event !== undefined
            ? this.entries.find(entry => entry.start <= target.event && entry.end >= target.event)?.element
            : this.turns.get(target.turn + ':' + target.role);
        if (!element?.isConnected) {
            this.failed('The matching history entry could not be located. Run the search again.');
            return;
        }
        this.pauseFollowing();
        let content = element;
        if (element.classList.contains('tool-call')) {
            element.querySelector('.tool-call-body').classList.add('open');
            element.querySelector('.arrow').classList.add('open');
            const output = ['tool:rawOutput', 'tool:content', 'tool:locations'].includes(target.source);
            element.querySelectorAll('.tc-tab')[output ? 1 : 0].click();
            const jsonToggle = element.querySelector('.tc-json-toggle');
            if (jsonToggle?.classList.contains('active')) jsonToggle.click();
            content = target.source === 'tool:title' ? element.querySelector('.tool-call-header') : element.querySelector('.tc-panel.active');
        }
        if (target.source === 'agent_thought_chunk') element.style.display = 'block';
        for (const code of content.querySelectorAll('code')) {
            if (code._fullText !== undefined) code.textContent = code._fullText;
        }
        for (const hidden of content.querySelectorAll('.msg.user > div[style]')) {
            if (hidden.style.display === 'none') hidden.style.display = '';
        }
        this.messages.querySelectorAll('.search-target').forEach(node => node.classList.remove('search-target'));
        element.classList.add('search-target');
        element.tabIndex = -1;
        element.focus({preventScroll: true});
        const range = this.highlight(content, target.text, target.occurrence || 0);
        if (range) {
            for (let parent = range.startContainer.parentElement; parent && parent !== this.messages; parent = parent.parentElement) {
                if (parent.scrollHeight <= parent.clientHeight || !['auto', 'scroll'].includes(getComputedStyle(parent).overflowY)) continue;
                parent.scrollTop += range.getBoundingClientRect().top - parent.getBoundingClientRect().top - 12;
            }
        }
        const rect = range?.getBoundingClientRect();
        const top = rect?.height ? rect.top : element.getBoundingClientRect().top;
        this.messages.scrollTop += top - this.messages.getBoundingClientRect().top - 24;
    }

    highlight(element, text, occurrence = 0) {
        if (window.CSS?.highlights) CSS.highlights.delete('chat-search-match');
        if (!text) return null;
        const nodes = [];
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node;
        let joined = '';
        while ((node = walker.nextNode())) {
            if (node.parentElement.closest('.tc-raw, .tc-panel:not(.active), script, style')) continue;
            nodes.push({node, start: joined.length});
            joined += node.textContent;
        }
        let index = -text.length;
        for (let count = 0; count <= occurrence; count++) {
            index = joined.toLowerCase().indexOf(text.toLowerCase(), index + text.length);
            if (index < 0) return null;
        }
        if (index < 0) return null;
        const first = nodes.find(entry => entry.start + entry.node.length > index);
        const last = nodes.find(entry => entry.start + entry.node.length >= index + text.length);
        const range = document.createRange();
        range.setStart(first.node, index - first.start);
        range.setEnd(last.node, index + text.length - last.start);
        if (window.CSS?.highlights) CSS.highlights.set('chat-search-match', new Highlight(range));
        return range;
    }
}
