const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');

test('opening discovers every server, searches unopened tools, and refreshes schemas on reopen', async () => {
    const dom = new JSDOM('<div id="toolModalOverlay"></div><div id="toolPickerView"></div><div id="toolExecView"></div><input id="toolSearch"><div id="toolList"></div>', {runScripts: 'outside-only'});
    const source = fs.readFileSync('src/templates/chat.html', 'utf8');
    const context = dom.getInternalVMContext();
    const requests = [];
    let version = 1;
    dom.window.fetch = async url => {
        requests.push(url);
        return {ok: true, headers: {get: () => 'application/json'}, json: async () => url === '/api/mcp/servers' ? {servers: ['enabled', 'disabled']} : {tools: [{name: url.includes('disabled') ? 'hidden_tool' : 'normal_tool', description: `version ${version}`, inputSchema: {}, server: url.split('=')[1]}]}};
    };
    vm.runInContext("let _mcpServers=null, _mcpToolsByServer={}, _mcpToolErrors={}, _mcpToolGeneration=0; const _mcpExpandedServers=new Set(); const apiKey='fixture'; function escapeHtml(value){return value;} function selectTool(){}\n" + source.slice(source.indexOf('    async function openToolModal()'), source.indexOf('    function selectTool(')), context);
    await dom.window.openToolModal();
    assert.equal(requests.length, 3);
    assert.ok(requests.includes('/api/mcp/tools?server=disabled'));
    dom.window.document.getElementById('toolSearch').value = 'hidden_tool';
    dom.window.filterTools();
    assert.match(dom.window.document.getElementById('toolList').textContent, /hidden_tool/);
    version = 2;
    await dom.window.openToolModal();
    dom.window.document.getElementById('toolSearch').value = 'hidden_tool';
    dom.window.filterTools();
    assert.match(dom.window.document.getElementById('toolList').textContent, /version 2/);
    assert.doesNotMatch(dom.window.document.getElementById('toolList').textContent, /version 1/);
    assert.equal(requests.length, 6);
    dom.window.fetch = async url => url === '/api/mcp/servers' ? {ok: true, headers: {get: () => 'application/json'}, json: async () => ({servers: ['broken']})} : {ok: false, status: 502, json: () => {throw new Error('HTML must not be parsed');}};
    await dom.window.openToolModal();
    assert.match(dom.window.document.getElementById('toolList').textContent, /broken \(HTTP 502\)/);
    assert.doesNotMatch(dom.window.document.getElementById('toolList').textContent, /HTML must not be parsed|Unexpected token/);
    dom.window.close();
});
