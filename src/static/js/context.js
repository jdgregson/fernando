/* Context: MCP servers, steering documents, and templates for group-scoped configuration. */
let contextConfig = null;

function contextAction(event, data) {
    return new Promise((resolve, reject) => {
        socket.timeout(15000).emit(event, {...data, csrf_token: csrfToken}, (error, result) => {
            if (error || !result || result.error) reject(new Error(result?.error || 'Context request failed'));
            else resolve(result);
        });
    });
}

async function contextRequest(path) {
    const response = await fetch(path, {
        headers: {'Content-Type': 'application/json', 'X-API-Key': window.FERNANDO_API_KEY},
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Context request failed');
    return data;
}

async function loadContextSettings() {
    try {
        contextConfig = await contextRequest('/api/context');
        renderMcpServerList();
        renderSteeringList();
        renderTemplatesList();
    } catch (error) {
        document.getElementById('mcpServerList').textContent = error.message;
        document.getElementById('steeringList').textContent = error.message;
        document.getElementById('templatesList').textContent = error.message;
    }
}

// ===== MCP Server List =====

let editingMcpId = null;

function renderMcpServerList() {
    const container = document.getElementById('mcpServerList');
    const servers = contextConfig?.servers || {};
    const serverIds = Object.keys(servers);
    
    let html = '';
    
    if (editingMcpId !== null) {
        const isNew = editingMcpId === 'new';
        const server = isNew ? {description: '', kiro: {command: '', args: []}} : servers[editingMcpId];
        const kiro = server.kiro || {};
        
        html = `
            <div class="template-form">
                <div class="settings-row">
                    <label class="settings-label">Server Name</label>
                    <input type="text" id="mcpNameInput" class="settings-input" style="width:100%;text-align:left;" value="${isNew ? '' : escapeHtml(editingMcpId)}" placeholder="e.g., my-server" ${isNew ? '' : 'disabled'}>
                </div>
                <div class="settings-row">
                    <label class="settings-label">Description</label>
                    <input type="text" id="mcpDescInput" class="settings-input" style="width:100%;text-align:left;" value="${escapeHtml(server.description || '')}" placeholder="What does this server do?">
                </div>
                <div class="settings-row">
                    <label class="settings-label">Command</label>
                    <input type="text" id="mcpCommandInput" class="settings-input" style="width:100%;text-align:left;" value="${escapeHtml(kiro.command || '')}" placeholder="e.g., node, python, npx">
                </div>
                <div class="settings-row">
                    <label class="settings-label">Arguments (one per line)</label>
                    <textarea id="mcpArgsInput" class="settings-input" style="width:100%;text-align:left;height:80px;resize:vertical;" placeholder="e.g., /path/to/server.js\n--port\n3000">${escapeHtml((kiro.args || []).join('\n'))}</textarea>
                </div>
                <div class="template-form-actions">
                    <button class="template-btn" onclick="cancelMcpEdit()">Cancel</button>
                    <button class="auth-add-btn" onclick="saveMcpServer()">Save</button>
                </div>
            </div>
        `;
    } else {
        if (!serverIds.length) {
            html = '<div style="color:#666;padding:10px 0;">No MCP servers configured.</div>';
        } else {
            html = `<div class="context-list-header">
                <span class="context-col-name">Server</span>
                <span class="context-col-enable">Enable (globally)</span>
                <span class="context-col-actions"></span>
            </div>`;
            html += serverIds.map(id => {
                const s = servers[id];
                return `<div class="context-list-row">
                    <label class="context-col-name" for="mcp_${id}"><div class="context-item-name">${escapeHtml(id)}</div><div class="context-item-desc">${escapeHtml(s.description || '')}</div></label>
                    <span class="context-col-enable"><input type="checkbox" id="mcp_${id}" ${s.global ? 'checked' : ''} onchange="toggleMcpServerGlobal('${id}', this.checked)"></span>
                    <span class="context-col-actions">
                        <button class="icon-btn" onclick="startEditMcpServer('${id}')" title="Edit"><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.5l2 2L5 13H3v-2l8.5-8.5z"/></svg></button>
                        <button class="icon-btn" onclick="removeMcpServer('${id}')" title="Remove"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg></button>
                    </span>
                </div>`;
            }).join('');
        }
        html += '<div class="settings-add-row"><button class="auth-add-btn" onclick="startNewMcpServer()">+ Add Server</button></div>';
    }
    
    container.innerHTML = html;
    
    if (editingMcpId !== null) {
        document.getElementById(editingMcpId === 'new' ? 'mcpNameInput' : 'mcpDescInput').focus();
    }
}

function startNewMcpServer() {
    editingMcpId = 'new';
    renderMcpServerList();
}

function startEditMcpServer(id) {
    editingMcpId = id;
    renderMcpServerList();
}

function cancelMcpEdit() {
    editingMcpId = null;
    renderMcpServerList();
}

async function saveMcpServer() {
    const nameInput = document.getElementById('mcpNameInput');
    const name = nameInput.value.trim();
    if (!name || !/^[a-zA-Z0-9_.-]+$/.test(name)) {
        nameInput.focus();
        showToast('Invalid server name');
        return;
    }
    
    const description = document.getElementById('mcpDescInput').value.trim();
    const command = document.getElementById('mcpCommandInput').value.trim();
    const argsText = document.getElementById('mcpArgsInput').value;
    const args = argsText.split('\n').map(a => a.trim()).filter(a => a);
    
    if (!command) {
        document.getElementById('mcpCommandInput').focus();
        showToast('Command is required');
        return;
    }
    
    const id = editingMcpId === 'new' ? name : editingMcpId;
    
    if (editingMcpId === 'new' && contextConfig.servers[id]) {
        showToast('Server with this name already exists');
        nameInput.focus();
        return;
    }
    
    contextConfig.servers[id] = {
        description,
        kiro: {command, args},
        global: contextConfig.servers[id]?.global || false
    };
    
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
        editingMcpId = null;
        renderMcpServerList();
    } catch (error) {
        showToast('Failed to save: ' + error.message);
    }
}

async function removeMcpServer(id) {
    delete contextConfig.servers[id];
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
        renderMcpServerList();
    } catch (error) {
        showToast('Failed to remove: ' + error.message);
    }
}

async function toggleMcpServerGlobal(serverId, global) {
    if (!contextConfig?.servers?.[serverId]) return;
    contextConfig.servers[serverId].global = global;
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
    } catch (error) {
        contextConfig.servers[serverId].global = !global;
        document.getElementById('mcp_' + serverId).checked = !global;
    }
}

// ===== Steering Documents List =====

function renderSteeringList() {
    const container = document.getElementById('steeringList');
    const documents = contextConfig?.documents || {};
    const documentIds = Object.keys(documents);
    
    let html = '';
    
    if (!documentIds.length) {
        html = '<div style="color:#666;padding:10px 0;">No steering documents configured.</div>';
    } else {
        html = `<div class="context-list-header">
            <span class="context-col-name">Document</span>
            <span class="context-col-enable">Enable (globally)</span>
            <span class="context-col-actions"></span>
        </div>`;
        html += documentIds.map(id => {
            const d = documents[id];
            return `<div class="context-list-row steering-row" data-id="${id}">
                <div class="context-col-name">
                    <input type="text" class="steering-inline-input" value="${escapeHtml(d.name || '')}" placeholder="Name" onchange="updateSteering('${id}', 'name', this.value)">
                    <input type="text" class="steering-inline-input path" value="${escapeHtml(d.path || '')}" placeholder="File path" onchange="updateSteering('${id}', 'path', this.value)">
                </div>
                <span class="context-col-enable"><input type="checkbox" id="doc_${id}" ${d.global ? 'checked' : ''} onchange="toggleSteeringGlobal('${id}', this.checked)"></span>
                <span class="context-col-actions"><button class="icon-btn" onclick="removeSteering('${id}')" title="Remove"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg></button></span>
            </div>`;
        }).join('');
    }
    html += '<div class="settings-add-row"><button class="auth-add-btn" onclick="addNewSteering()">+ Add Document</button></div>';
    
    container.innerHTML = html;
}

async function addNewSteering() {
    const id = crypto.randomUUID().slice(0, 12);
    contextConfig.documents[id] = {name: '', path: '', global: false};
    renderSteeringList();
    const input = document.querySelector(`.steering-row[data-id="${id}"] input[type="text"]`);
    if (input) input.focus();
}

async function toggleSteeringGlobal(docId, global) {
    if (!contextConfig?.documents?.[docId]) return;
    contextConfig.documents[docId].global = global;
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
    } catch (error) {
        contextConfig.documents[docId].global = !global;
        document.getElementById('doc_' + docId).checked = !global;
    }
}

async function updateSteering(id, field, value) {
    if (!contextConfig.documents[id]) return;
    contextConfig.documents[id][field] = value;
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
    } catch (error) {
        showToast('Failed to save: ' + error.message);
    }
}

async function removeSteering(id) {
    delete contextConfig.documents[id];
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
        renderSteeringList();
    } catch (error) {
        showToast('Failed to remove: ' + error.message);
    }
}

// ===== Templates List =====

let editingTemplateId = null;

function renderTemplatesList() {
    const container = document.getElementById('templatesList');
    const templates = contextConfig?.templates || {};
    const templateIds = Object.keys(templates);
    const servers = contextConfig?.servers || {};
    const documents = contextConfig?.documents || {};
    
    let html = '';
    
    if (editingTemplateId !== null) {
        const isNew = editingTemplateId === 'new';
        const template = isNew ? {name: '', servers: [], documents: []} : templates[editingTemplateId];
        
        html = `
            <div class="template-form">
                <div class="settings-row">
                    <label class="settings-label">Template Name</label>
                    <input type="text" id="templateNameInput" class="settings-input" style="width:100%;text-align:left;" value="${escapeHtml(template.name)}" placeholder="e.g., Fernando Development">
                </div>
                <div class="settings-row">
                    <label class="settings-label">MCP Servers</label>
                    <div class="template-checklist" id="templateServersChecklist">
                        ${Object.keys(servers).map(id => `
                            <label class="template-check-item">
                                <input type="checkbox" value="${id}" ${template.servers?.includes(id) ? 'checked' : ''}>
                                <span>${escapeHtml(id)}</span>
                            </label>
                        `).join('') || '<div style="color:#666">No servers registered</div>'}
                    </div>
                </div>
                <div class="settings-row">
                    <label class="settings-label">Steering Documents</label>
                    <div class="template-checklist" id="templateDocsChecklist">
                        ${Object.entries(documents).map(([id, doc]) => `
                            <label class="template-check-item">
                                <input type="checkbox" value="${id}" ${template.documents?.includes(id) ? 'checked' : ''}>
                                <span>${escapeHtml(doc.name || id)}</span>
                            </label>
                        `).join('') || '<div style="color:#666">No steering documents registered</div>'}
                    </div>
                </div>
                <div class="template-form-actions">
                    <button class="template-btn" onclick="cancelTemplateEdit()">Cancel</button>
                    <button class="auth-add-btn" onclick="saveTemplate()">Save</button>
                </div>
            </div>
        `;
    } else {
        if (!templateIds.length) {
            html = '<div style="color:#666;padding:10px 0;">No templates configured.</div>';
        } else {
            html = `<div class="context-list-header">
                <span class="context-col-name">Template</span>
                <span class="context-col-meta">Contents</span>
                <span class="context-col-actions"></span>
            </div>`;
            html += templateIds.map(id => {
                const t = templates[id];
                const serverCount = (t.servers || []).length;
                const docCount = (t.documents || []).length;
                return `<div class="context-list-row">
                    <span class="context-col-name"><div class="context-item-name">${escapeHtml(t.name)}</div></span>
                    <span class="context-col-meta">${serverCount} server${serverCount !== 1 ? 's' : ''}, ${docCount} doc${docCount !== 1 ? 's' : ''}</span>
                    <span class="context-col-actions">
                        <button class="icon-btn" onclick="startEditTemplate('${id}')" title="Edit"><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.5l2 2L5 13H3v-2l8.5-8.5z"/></svg></button>
                        <button class="icon-btn" onclick="deleteTemplate('${id}')" title="Remove"><svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg></button>
                    </span>
                </div>`;
            }).join('');
        }
        html += '<div class="settings-add-row"><button class="auth-add-btn" onclick="startNewTemplate()">+ New Template</button></div>';
    }
    
    container.innerHTML = html;
    
    if (editingTemplateId !== null) {
        document.getElementById('templateNameInput').focus();
    }
}

function startNewTemplate() {
    editingTemplateId = 'new';
    renderTemplatesList();
}

function startEditTemplate(id) {
    editingTemplateId = id;
    renderTemplatesList();
}

function cancelTemplateEdit() {
    editingTemplateId = null;
    renderTemplatesList();
}

async function saveTemplate() {
    const name = document.getElementById('templateNameInput').value.trim();
    if (!name) {
        document.getElementById('templateNameInput').focus();
        return;
    }
    
    const servers = Array.from(document.querySelectorAll('#templateServersChecklist input:checked')).map(cb => cb.value);
    const documents = Array.from(document.querySelectorAll('#templateDocsChecklist input:checked')).map(cb => cb.value);
    
    const id = editingTemplateId === 'new' ? crypto.randomUUID().slice(0, 12) : editingTemplateId;
    contextConfig.templates[id] = {name, servers, documents};
    
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
        editingTemplateId = null;
        renderTemplatesList();
    } catch (error) {
        showToast('Failed to save: ' + error.message);
    }
}

async function deleteTemplate(templateId) {
    delete contextConfig.templates[templateId];
    try {
        contextConfig = await contextAction('context_save', {config: contextConfig});
        renderTemplatesList();
    } catch (error) {
        showToast('Failed to delete: ' + error.message);
    }
}

// ===== Group Templates Submenu =====

let activeSubmenu = null;
let submenuRequest = 0;

async function showGroupTemplatesSubmenu(groupId, parentMenu, x, y) {
    closeActiveSubmenu();
    const request = submenuRequest;
    
    try {
        const config = await contextRequest('/api/context');
        if (request !== submenuRequest || !parentMenu.isConnected) return;
        const group = _cachedGroups.find(g => g.id === groupId);
        if (!group) return;
        
        const templates = Object.entries(config.templates || {});
        const submenu = document.createElement('div');
        submenu.className = 'group-context-menu template-submenu';
        submenu.style.left = x + 'px';
        submenu.style.top = y + 'px';
        
        let selected = new Set((group.template_ids || []).filter(id => config.templates[id]));
        
        if (!templates.length) {
            const emptyItem = document.createElement('div');
            emptyItem.className = 'context-menu-item';
            emptyItem.style.color = '#666';
            emptyItem.style.cursor = 'default';
            emptyItem.textContent = 'No templates';
            submenu.appendChild(emptyItem);
        } else {
            for (const [id, template] of templates) {
                const item = document.createElement('div');
                item.className = 'context-menu-item template-item';
                
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = selected.has(id);
                checkbox.id = 'tpl_' + id;
                checkbox.onchange = async () => {
                    if (checkbox.checked) selected.add(id);
                    else selected.delete(id);
                    try {
                        const result = await contextAction('group_set_templates', {
                            group_id: groupId, 
                            template_ids: Array.from(selected)
                        });
                        Object.assign(group, result.group);
                    } catch (error) {
                        checkbox.checked = !checkbox.checked;
                        if (checkbox.checked) selected.add(id);
                        else selected.delete(id);
                    }
                };
                
                const label = document.createElement('label');
                label.htmlFor = 'tpl_' + id;
                label.textContent = template.name;
                
                item.appendChild(checkbox);
                item.appendChild(label);
                submenu.appendChild(item);
            }
        }
        
        document.body.appendChild(submenu);
        activeSubmenu = submenu;
        
        const rect = submenu.getBoundingClientRect();
        if (rect.right > window.innerWidth) submenu.style.left = (window.innerWidth - rect.width - 10) + 'px';
        if (rect.bottom > window.innerHeight) submenu.style.top = (window.innerHeight - rect.height - 10) + 'px';
        
    } catch (error) {
        if (request === submenuRequest && parentMenu.isConnected) showToast('Failed to load templates');
    }
}

function closeActiveSubmenu() {
    submenuRequest++;
    if (activeSubmenu) {
        activeSubmenu.remove();
        activeSubmenu = null;
    }
}

// ===== Utilities =====

function showToast(message) {
    let toast = document.getElementById('fernandoToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'fernandoToast';
        toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1a2236;border:1px solid #2a3a5c;color:#d4d4d4;padding:10px 20px;border-radius:6px;font-size:13px;z-index:10000;opacity:0;transition:opacity 0.3s;pointer-events:none;';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.opacity = '1';
    setTimeout(() => { toast.style.opacity = '0'; }, 3000);
}

// ===== Chat Context Inspection =====

async function inspectChatContext(sessionId) {
    try {
        const context = await contextRequest(`/api/chats/${encodeURIComponent(sessionId)}/context`);
        showContextInfoModal(context);
    } catch (error) {
        showToast('Failed to load context: ' + error.message);
    }
}

function showContextInfoModal(context) {
    let overlay = document.getElementById('contextInfoOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'contextInfoOverlay';
        overlay.className = 'modal';
        overlay.onclick = e => { if (e.target === overlay) overlay.classList.remove('open'); };
        
        const modal = document.createElement('div');
        modal.className = 'modal-content';
        modal.onclick = e => e.stopPropagation();
        modal.innerHTML = `
            <div class="modal-header">Chat Context</div>
            <div class="modal-body" id="contextInfoBody"></div>
        `;
        
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
    }
    
    const body = document.getElementById('contextInfoBody');
    if (!context.managed) {
        body.innerHTML = '<p style="color:#888">Legacy chat: using its original harness configuration.</p>';
    } else {
        body.innerHTML = `
            <div style="margin-bottom:12px;">
                <div style="color:#6a7a8a;font-size:11px;text-transform:uppercase;margin-bottom:4px;">Templates</div>
                <div style="color:#d4d4d4;">${context.templates?.map(t => t.name).join(', ') || 'None'}</div>
            </div>
            <div style="margin-bottom:12px;">
                <div style="color:#6a7a8a;font-size:11px;text-transform:uppercase;margin-bottom:4px;">Steering Files</div>
                <div style="color:#d4d4d4;">${context.documents?.map(d => d.name).join(', ') || 'None'}</div>
            </div>
            <div style="margin-bottom:12px;">
                <div style="color:#6a7a8a;font-size:11px;text-transform:uppercase;margin-bottom:4px;">MCP Servers</div>
                <div style="color:#d4d4d4;">${context.servers?.join(', ') || 'None'}</div>
            </div>
            <div>
                <div style="color:#6a7a8a;font-size:11px;text-transform:uppercase;margin-bottom:4px;">Settings Revision</div>
                <div style="color:#d4d4d4;">${context.revision || 'Unknown'}</div>
            </div>
        `;
    }
    
    overlay.classList.add('open');
}

async function applyChatContext(sessionId) {
    if (!confirm('Reload this chat with current global and group context?')) return;
    try {
        await contextAction('acp_apply_context', {session_id: sessionId});
        showToast('Context applied');
    } catch (error) {
        showToast('Failed: ' + error.message);
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
