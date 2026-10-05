function bindModalBackdrop(overlay, dismiss) {
    bindBackdropDismissal(overlay, target => target === overlay, dismiss);
}

function bindBackdropDismissal(root, isBackdrop, dismiss) {
    const doc = root.nodeType === 9 ? root : root.ownerDocument;
    let pointerId = null;
    let releasedOnBackdrop = false;

    root.addEventListener('pointerdown', event => {
        pointerId = isBackdrop(event.target) && event.button === 0 && event.isPrimary
            ? event.pointerId : null;
        releasedOnBackdrop = false;
    }, true);

    root.addEventListener('pointerup', event => {
        releasedOnBackdrop = pointerId !== null && event.pointerId === pointerId
            && isBackdrop(event.target)
            && isBackdrop(doc.elementFromPoint(event.clientX, event.clientY));
    }, true);

    root.addEventListener('pointercancel', () => {
        pointerId = null;
        releasedOnBackdrop = false;
    }, true);

    root.addEventListener('click', event => {
        const shouldDismiss = releasedOnBackdrop && isBackdrop(event.target) && event.button === 0;
        pointerId = null;
        releasedOnBackdrop = false;
        if (shouldDismiss) dismiss();
    }, true);
}
