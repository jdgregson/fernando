(() => {
    const type = 'application/x-fernando-files';
    const prefix = 'fernando-file-drag:';
    const lifetime = 60000;
    function create(entries, kind = 'files') {
        for (let index = localStorage.length - 1; index >= 0; index--) {
            const key = localStorage.key(index);
            if (key.startsWith(prefix) && Date.now() - JSON.parse(localStorage.getItem(key)).created > lifetime) localStorage.removeItem(key);
        }
        const id = crypto.randomUUID();
        localStorage.setItem(prefix + id, JSON.stringify({created:Date.now(), kind, entries}));
        return id;
    }
    function take(transfer) {
        const id = transfer.getData(type);
        if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) throw new Error('Invalid Fernando file drag');
        const stored = localStorage.getItem(prefix + id);
        if (!stored) throw new Error('This file drag is not from an active Fernando view');
        localStorage.removeItem(prefix + id);
        const data = JSON.parse(stored);
        if (!Number.isFinite(data.created) || Date.now() - data.created > lifetime || data.created > Date.now()) throw new Error('This file drag has expired; drag the items again');
        if (!['files','references'].includes(data.kind) || !Array.isArray(data.entries) || !data.entries.length || !data.entries.every(entry => entry && typeof entry.path === 'string' && entry.path.startsWith('/') && !entry.path.includes('\0') && typeof entry.name === 'string')) throw new Error('Invalid file references');
        return data;
    }
    function discard(id) {
        if (id) localStorage.removeItem(prefix + id);
    }
    function bindSource(element, getEntries, kind, onError) {
        let id = null;
        element.draggable = true;
        element.addEventListener('dragstart', event => {
            try {
                id = create(getEntries(), kind);
                event.dataTransfer.setData(type, id);
                event.dataTransfer.effectAllowed = kind === 'references' ? 'copy' : 'copyMove';
            } catch (error) {
                event.preventDefault();
                onError(error);
            }
        });
        element.addEventListener('dragend', () => { discard(id); id = null; });
    }
    window.FernandoFileDrag = Object.freeze({type, create, take, discard, bindSource, accepts: transfer => [...transfer.types].includes(type)});
})();
