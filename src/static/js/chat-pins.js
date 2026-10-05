class ChatPins {
    static pinIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m16 3 5 5-4 1-4 4 1 4-2 2-7-7 2-2 4 1 4-4zM5 19l-3 3"/></svg>';

    constructor({messages, navigation, save, pauseFollowing, bottom}) {
        this.messages = messages;
        this.save = save;
        this.pauseFollowing = pauseFollowing;
        this.bottom = bottom;
        this.pins = new Set();
        this.pending = new Set();
        this.registered = new Set();
        this.frame = null;
        this.up = this.makeButton('Previous pinned message', 'up', true);
        this.down = this.makeButton('Next pinned message', 'down', true);
        this.end = this.makeButton('Jump to bottom', 'down', false);
        this.up.classList.add('pin-nav-top');
        this.end.classList.add('pin-nav-end');
        const lower = document.createElement('div');
        lower.className = 'pin-nav-bottom';
        lower.append(this.down);
        navigation.append(this.up, lower, this.end);
        this.up.onclick = () => this.jump(this.above);
        this.down.onclick = () => this.jump(this.below);
        this.end.onclick = () => { this.bottom(); this.schedule(); };
        messages.addEventListener('scroll', () => this.schedule(), {passive: true});
        this.resize = new ResizeObserver(() => this.schedule());
        this.resize.observe(messages);
        this.mutations = new MutationObserver(() => this.schedule());
        this.mutations.observe(messages, {childList: true, subtree: true});
        this.schedule();
    }

    makeButton(label, direction, pin) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'pin-nav-button';
        button.title = label;
        button.setAttribute('aria-label', label);
        button.hidden = true;
        const path = direction === 'up' ? 'm6 14 6-6 6 6M12 8v12' : 'm6 10 6 6 6-6M12 4v12';
        button.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>` + (pin ? ChatPins.pinIcon : '');
        return button;
    }

    register(message, role, turn) {
        const ordinal = this.messages.querySelectorAll(`.msg.${role}[data-turn="${turn}"]`).length;
        message.dataset.pinKey = `${role}:${turn}:${ordinal}`;
        this.registered.add(message);
        this.resize.observe(message);
        this.render(message);
    }

    receive(pins) {
        this.pins = new Set(pins);
        for (const message of this.registered) {
            if (message.isConnected) this.render(message);
        }
        this.schedule();
    }

    render(message) {
        const pinned = this.pins.has(message.dataset.pinKey);
        message.classList.toggle('pinned', pinned);
        const label = message.querySelector('.msg-label');
        let button = label.querySelector('.message-unpin');
        if (pinned && !button) {
            button = document.createElement('button');
            button.type = 'button';
            button.className = 'message-unpin';
            button.title = 'Unpin message';
            button.setAttribute('aria-label', 'Unpin message');
            button.innerHTML = ChatPins.pinIcon;
            button.onclick = event => { event.stopPropagation(); this.toggle(message); };
            button.addEventListener('touchstart', event => event.stopPropagation(), {passive: true});
            label.insertBefore(button, label.querySelector('.msg-ts'));
        } else if (!pinned && button) {
            button.remove();
        }
    }

    toggle(message) {
        const key = message.dataset.pinKey;
        if (this.pending.has(key)) return;
        this.pending.add(key);
        this.save(key, !this.pins.has(key), result => {
            this.pending.delete(key);
            if (result) this.receive(result);
        });
    }

    schedule() {
        if (this.frame !== null) return;
        this.frame = requestAnimationFrame(() => { this.frame = null; this.update(); });
    }

    update() {
        for (const message of this.registered) {
            if (!message.isConnected) {
                this.resize.unobserve(message);
                this.registered.delete(message);
            }
        }
        const viewport = this.messages.getBoundingClientRect();
        this.above = null;
        this.below = null;
        for (const message of this.messages.querySelectorAll('.msg.pinned')) {
            const rect = message.getBoundingClientRect();
            if (rect.bottom <= viewport.top + 1) this.above = message;
            else if (rect.top >= viewport.bottom - 1 && !this.below) this.below = message;
        }
        this.up.hidden = !this.above;
        this.down.hidden = !this.below;
        this.end.hidden = this.messages.scrollHeight - this.messages.scrollTop - this.messages.clientHeight <= 3;
    }

    jump(message) {
        if (!message) return;
        this.pauseFollowing();
        this.messages.scrollTop += message.getBoundingClientRect().top - this.messages.getBoundingClientRect().top - 12;
        this.update();
    }
}
