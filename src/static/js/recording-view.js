(function () {
    function manifestFrom(value, depth = 0) {
        if (!value || depth > 8) return null;
        if (typeof value === 'string') {
            const line = value.split('\n').find(line => line.startsWith('FERNANDO_RECORDING: '));
            if (line) {
                try {
                    return JSON.parse(line.slice('FERNANDO_RECORDING: '.length));
                } catch {
                    return null;
                }
            }
            if (value.trimStart().startsWith('{') || value.trimStart().startsWith('[')) {
                try {
                    return manifestFrom(JSON.parse(value), depth + 1);
                } catch {
                    return null;
                }
            }
            return null;
        }
        if (Array.isArray(value)) {
            for (const item of value) {
                const result = manifestFrom(item, depth + 1);
                if (result) return result;
            }
            return null;
        }
        if (typeof value === 'object') {
            for (const key of ['items', 'Json', 'json', 'content', 'Content', 'output', 'text', 'Text']) {
                const result = manifestFrom(value[key], depth + 1);
                if (result) return result;
            }
        }
        return null;
    }

    function validManifest(value) {
        return value && /^[0-9a-f]{32}$/.test(value.recording_id)
            && typeof value.path === 'string' && /^\/[^\r\n]+\.mp4$/.test(value.path)
            && Number.isFinite(value.duration) && value.duration > 0
            && ['inline', 'native-read'].includes(value.delivery)
            && Array.isArray(value.frames) && value.frames.length > 0 && value.frames.length <= 12
            && value.frames.every(frame => typeof frame.path === 'string' && /^\/[^\r\n]+\.png$/.test(frame.path)
                && Number.isFinite(frame.seconds) && frame.seconds >= 0 && frame.seconds < value.duration);
    }

    function render(tc, content, fileUrl) {
        const manifest = manifestFrom(content) || manifestFrom(tc.tcState.rawOutput);
        if (!validManifest(manifest)) return Boolean(tc.recordingPreview);
        const signature = JSON.stringify(manifest);
        if (tc.recordingSignature === signature) return true;
        tc.recordingCleanup?.();
        tc.recordingPreview?.remove();
        tc.imagePreview?.remove();
        tc.imagePreview = null;
        const root = document.createElement('div');
        root.className = 'recording-preview';
        const tabs = document.createElement('div');
        tabs.className = 'recording-tabs';
        tabs.setAttribute('role', 'tablist');
        tabs.setAttribute('aria-label', 'Recording views');
        const videoPanel = document.createElement('div');
        const framesPanel = document.createElement('div');
        const id = 'recording-' + crypto.randomUUID();
        videoPanel.id = id + '-video';
        framesPanel.id = id + '-frames';
        videoPanel.setAttribute('role', 'tabpanel');
        framesPanel.setAttribute('role', 'tabpanel');
        const video = document.createElement('video');
        video.controls = true;
        video.playsInline = true;
        video.preload = 'metadata';
        video.src = fileUrl(manifest.path) + '&inline=1';
        video.setAttribute('aria-label', 'Desktop recording');
        const stage = document.createElement('div');
        stage.className = 'recording-stage';
        const backdrop = document.createElement('canvas');
        backdrop.className = 'recording-backdrop';
        backdrop.setAttribute('aria-hidden', 'true');
        const context = backdrop.getContext('2d', { alpha: false });
        let frameCallback = null;
        function paintBackdrop() {
            if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
            const height = Math.max(1, Math.round(320 * video.videoHeight / video.videoWidth));
            if (backdrop.width !== 320 || backdrop.height !== height) {
                backdrop.width = 320;
                backdrop.height = height;
            }
            context.drawImage(video, 0, 0, backdrop.width, backdrop.height);
        }
        function stopBackdrop() {
            if (frameCallback !== null) video.cancelVideoFrameCallback(frameCallback);
            frameCallback = null;
        }
        function nextFrame() {
            frameCallback = null;
            if (!stage.isConnected) return;
            paintBackdrop();
            if (!video.paused && !video.ended) frameCallback = video.requestVideoFrameCallback(nextFrame);
        }
        video.addEventListener('loadeddata', paintBackdrop);
        video.addEventListener('seeked', paintBackdrop);
        video.addEventListener('playing', () => {
            stopBackdrop();
            paintBackdrop();
            frameCallback = video.requestVideoFrameCallback(nextFrame);
        });
        for (const event of ['pause', 'ended', 'error', 'emptied']) video.addEventListener(event, stopBackdrop);
        tc.recordingCleanup = () => { stopBackdrop(); video.pause(); };
        stage.append(backdrop, video);
        videoPanel.appendChild(stage);
        const error = document.createElement('p');
        error.className = 'recording-note';
        error.hidden = true;
        error.textContent = 'Recording unavailable or could not be played.';
        video.addEventListener('error', () => { error.hidden = false; });
        videoPanel.appendChild(error);
        const download = document.createElement('a');
        download.href = fileUrl(manifest.path);
        download.textContent = 'Download MP4';
        download.className = 'recording-download';
        download.download = 'recording-' + manifest.recording_id + '.mp4';
        videoPanel.appendChild(download);
        const note = document.createElement('p');
        note.className = 'recording-note';
        note.textContent = manifest.delivery === 'inline'
            ? 'These timestamped frames were returned as images to the agent.'
            : 'These frame paths were supplied to Kiro for native image reading. Its image-read calls show which frames it opened.';
        framesPanel.appendChild(note);
        const grid = document.createElement('div');
        grid.className = 'recording-frames';
        for (const frame of manifest.frames) {
            const figure = document.createElement('figure');
            const img = document.createElement('img');
            img.src = fileUrl(frame.path);
            img.loading = 'lazy';
            img.alt = 'Recording frame at ' + frame.seconds.toFixed(3) + ' seconds';
            const caption = document.createElement('figcaption');
            caption.textContent = frame.seconds.toFixed(3) + ' s';
            figure.append(img, caption);
            grid.appendChild(figure);
        }
        framesPanel.appendChild(grid);
        const buttons = ['Recording', 'Model frames'].map((label, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = label;
            button.id = id + '-tab-' + index;
            button.setAttribute('role', 'tab');
            button.setAttribute('aria-controls', index === 0 ? videoPanel.id : framesPanel.id);
            button.addEventListener('click', () => select(index));
            button.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index;
                select(next);
                buttons[next].focus();
            });
            tabs.appendChild(button);
            return button;
        });
        function select(index) {
            videoPanel.hidden = index !== 0;
            framesPanel.hidden = index !== 1;
            buttons.forEach((button, i) => {
                button.setAttribute('aria-selected', String(i === index));
                button.tabIndex = i === index ? 0 : -1;
            });
            if (index !== 0) video.pause();
        }
        videoPanel.setAttribute('aria-labelledby', buttons[0].id);
        framesPanel.setAttribute('aria-labelledby', buttons[1].id);
        select(0);
        root.append(tabs, videoPanel, framesPanel);
        tc.div.appendChild(root);
        tc.recordingPreview = root;
        tc.recordingSignature = signature;
        frameCallback = video.requestVideoFrameCallback(nextFrame);
        return true;
    }

    window.FernandoRecording = { render, manifestFrom, validManifest };
})();
