const fs = require('node:fs');
const source = fs.readFileSync('src/templates/chat.html', 'utf8');
const state = source.slice(source.indexOf('    let _userScrolledUp = false;'), source.indexOf('    let _suppressScroll = false;'));
const follow = source.slice(source.indexOf('    function scrollToBottom(force)'), source.indexOf('    function collapseCodeBlocks('));
const pill = fs.readFileSync('src/static/js/scroll-pill.js', 'utf8');
const script = `
const CopyControls = {observe() {}};
${state}
let _suppressScroll = false;
${follow}
${pill}
const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
const results = [];
function check(name, condition) {
    results.push({name, passed: Boolean(condition), top: messages.scrollTop, bottom: messages.scrollHeight - messages.clientHeight});
    if (!condition) throw new Error(name);
}
function grow(height = 60) {
    const card = document.createElement('div');
    card.style.height = height + 'px';
    card.textContent = 'Tool update ' + messages.children.length;
    messages.append(card);
    scrollToBottom();
}
window.run = async () => {
    messages.replaceChildren();
    grow(1200);
    scrollToBottom(true);
    await frame();
    for (let i = 0; i < 50; i++) {
        messages.firstElementChild.style.height = (i % 2 ? 1200 : 600) + 'px';
        void messages.scrollTop;
        grow();
        await frame();
    }
    check('50 real shrink/grow layouts keep following', Math.abs(messages.scrollTop - messages.scrollHeight + messages.clientHeight) <= 1);
    messages.dispatchEvent(new WheelEvent('wheel', {deltaY: -4}));
    messages.scrollTop -= 4;
    const reading = messages.scrollTop;
    grow();
    await frame();
    for (let i = 0; i < 20; i++) grow();
    await frame();
    check('slight user scroll remains paused across 21 updates', messages.scrollTop === reading);
    messages.dispatchEvent(new WheelEvent('wheel', {deltaY: 100}));
    messages.scrollTop = messages.scrollHeight - messages.clientHeight - 8;
    await frame();
    grow();
    await frame();
    check('returning within bottom area resumes following', Math.abs(messages.scrollTop - messages.scrollHeight + messages.clientHeight) <= 1);
    const pill = document.querySelector('.scroll-pill');
    pill.dispatchEvent(new MouseEvent('mousedown', {clientY: 200, bubbles: true}));
    document.dispatchEvent(new MouseEvent('mousemove', {clientY: 100, bubbles: true}));
    document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
    const dragged = messages.scrollTop;
    grow();
    await frame();
    check('actual scroll-pill drag pauses following', messages.scrollTop === dragged && _userScrolledUp);
    scrollToBottom(true);
    await frame();
    grow();
    await frame();
    check('explicit bottom navigation resumes following', Math.abs(messages.scrollTop - messages.scrollHeight + messages.clientHeight) <= 1);
    document.getElementById('result').textContent = JSON.stringify(results, null, 2);
    return results;
};
`;
fs.writeFileSync('/tmp/opencode/chat-scroll-browser.html', `<!doctype html><meta charset="utf-8"><title>Chat scroll regression fixture</title><style>body{background:#171717;color:#eee;font:16px sans-serif}#messages{height:400px;width:600px;overflow:auto;background:#292929}#messages>div{border-bottom:1px solid #555;box-sizing:border-box}.scroll-pill{position:absolute;right:0;width:8px;background:#888}.scroll-pill.dragging{background:#aaa}</style><h1>Chat scroll regression fixture</h1><div style="position:relative;width:620px"><div id="messages"></div></div><pre id="result"></pre><script>${script.replace(/<\/script/gi, '<\\/script')}</script>`);
