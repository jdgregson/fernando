class ChatImages {
    constructor(root, onLoad) {
        this.root = root;
        this.onLoad = onLoad;
        this.pending = new Set();
        this.sources = new WeakMap();
        this.paused = true;
        this.frame = null;
        this.observer = new IntersectionObserver(entries => {
            if (this.paused) return;
            for (const entry of entries) {
                if (entry.isIntersecting) this.load(entry.target);
            }
        }, {root, threshold: 0});
    }

    set(image, source) {
        image.removeAttribute('src');
        image.removeAttribute('srcset');
        image.decoding = 'async';
        image.classList.add('chat-image-pending');
        this.sources.set(image, source);
        this.pending.add(image);
        image.addEventListener('load', () => {
            image.classList.remove('chat-image-pending');
            this.onLoad();
        }, {once: true});
        image.addEventListener('error', () => {
            image.classList.remove('chat-image-pending');
            image.alt = 'Image unavailable';
        }, {once: true});
        this.schedule();
    }

    load(image) {
        if (!this.pending.delete(image)) return;
        this.observer.unobserve(image);
        image.src = this.sources.get(image);
        this.sources.delete(image);
    }

    pause() {
        this.paused = true;
        this.observer.disconnect();
        cancelAnimationFrame(this.frame);
        this.frame = null;
    }

    resume() {
        this.paused = false;
        this.schedule();
    }

    schedule() {
        if (this.paused || this.frame !== null) return;
        this.frame = requestAnimationFrame(() => {
            this.frame = null;
            for (const image of this.pending) {
                if (!this.root.contains(image)) {
                    this.pending.delete(image);
                    this.observer.unobserve(image);
                } else {
                    this.observer.observe(image);
                }
            }
            const images = Array.from(this.root.querySelectorAll('img')).filter(image => image.getClientRects().length);
            images.slice(-3).forEach(image => this.load(image));
        });
    }
}

window.ChatImages = ChatImages;
