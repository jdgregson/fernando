(() => {
    const applyGroupColor = payload => {
        const color = payload?.group?.color;
        if (typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color)) {
            document.documentElement.style.setProperty('--fernando-group-color', color);
        } else {
            document.documentElement.style.removeProperty('--fernando-group-color');
        }
    };
    FernandoPane.on('state', applyGroupColor);
    FernandoPane.on('context', applyGroupColor);
    FernandoPane.requestContext();
})();
