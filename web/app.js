'use strict';
const $ = id => document.getElementById(id);
let token = '', sessionId = null, busy = false, online = false;
let lastReady = false;
// The last exchange that Regénérer / Modifier may replace: { text, elements } or null.
let last = null, editing = false, controller = null, pendingImport = null;
const controls = [
    'userInput', 'sendBtn', 'sessions', 'newBtn', 'exportBtn', 'memoryBtn', 'helpBtn', 'ideaBtn', 'projectBtn', 'talkBtn',
    'settingsModel', 'renameBtn', 'deleteBtn', 'searchInput', 'regenBtn', 'editBtn', 'exportAllBtn', 'importBtn',
];
const isCommand = text => text.startsWith('/') || /^(retiens|oublie) ceci ?:/i.test(text);
function renderControls() {
    const disabled = busy || !online;
    for (const id of controls) $(id).disabled = disabled;
    // Typing stays possible while editing; the stop button replaces send during a reply.
    $('stopBtn').hidden = !controller; $('sendBtn').hidden = Boolean(controller);
    $('regenBtn').hidden = $('editBtn').hidden = !last || editing || Boolean(controller);
    $('editBanner').hidden = !editing;
    $('memoryForm').querySelectorAll('input,button').forEach(el => el.disabled = disabled);
    $('facts').querySelectorAll('button').forEach(el => el.disabled = disabled);
    $('statusText').textContent = busy ? 'AIRI réfléchit…' : (online ? 'En ligne' : 'Serveur indisponible');
    $('statusDot').classList.toggle('thinking', busy);
    $('statusDot').style.backgroundColor = online ? '' : '#ef4444';
}
function setBusy(value) {
    busy = value;
    renderControls();
}
async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', 'X-Airi-Token': token, ...options.headers } });
    if (!response.ok) {
        let detail = 'La requête a échoué.';
        try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : 'Données invalides.'; } catch (_) { /* No JSON body */ }
        throw new Error(detail);
    }
    return response;
}
function appendMessage(text, sender, extra = '') {
    $('welcome').hidden = true;
    const div = document.createElement('div');
    div.className = `message ${sender} ${extra}`;
    div.textContent = text;
    $('messagesArea').appendChild(div);
    $('conversationBody').scrollTop = $('conversationBody').scrollHeight;
    return div;
}
function error(message) {
    appendMessage(message, 'airi', 'error');
    $('announcement').textContent = message;
}
async function refreshSessions() {
    const sessions = await (await api('/api/sessions')).json();
    $('sessions').replaceChildren();
    for (const session of sessions) {
        const option = document.createElement('option');
        option.value = session.id; option.textContent = session.title;
        $('sessions').appendChild(option);
    }
    $('sessions').value = sessionId;
    return sessions;
}
async function loadSession(id) {
    const data = await (await api(`/api/sessions/${encodeURIComponent(id)}`)).json();
    sessionId = data.id;
    try {
        if (globalThis.airiDesktop) await globalThis.airiDesktop.saveSession(sessionId);
        else localStorage.setItem('airi.session', sessionId);
    } catch (_) { /* Optional preference */ }
    $('messagesArea').replaceChildren();
    const elements = [];
    for (const message of data.messages) {
        const classes = [message.kind === 'command' ? 'command' : '', message.interrupted ? 'interrupted' : ''].join(' ').trim();
        elements.push(appendMessage(message.content, message.role === 'user' ? 'user' : 'airi', classes));
    }
    const [question, answer] = data.messages.slice(-2);
    last = answer && answer.role === 'assistant' && answer.kind !== 'command'
        ? { text: question.content, elements: elements.slice(-2) } : null;
    editing = false;
    $('welcome').hidden = data.messages.length > 0;
    $('sessions').value = sessionId;
    renderControls();
}
// AIRI only proposes; the fact is saved solely when the user clicks « Retenir ».
function showSuggestion(fact) {
    const card = document.createElement('div');
    card.className = 'memory-suggestion';
    card.setAttribute('role', 'group');
    card.setAttribute('aria-label', 'Suggestion de souvenir');
    const text = document.createElement('span');
    text.textContent = `Retenir ce souvenir ? « ${fact} »`;
    const keep = document.createElement('button');
    keep.textContent = 'Retenir';
    const dismiss = document.createElement('button');
    dismiss.textContent = 'Non merci';
    dismiss.className = 'secondary';
    keep.addEventListener('click', () => action(async () => {
        await api('/api/memory', { method: 'POST', body: JSON.stringify({ fact }) });
        card.replaceChildren(Object.assign(document.createElement('span'), { textContent: `Souvenir enregistré : ${fact}` }));
        if (!$('memoryPanel').hidden) await refreshMemory();
    }));
    dismiss.addEventListener('click', () => card.remove());
    card.appendChild(text); card.appendChild(keep); card.appendChild(dismiss);
    $('messagesArea').appendChild(card);
    $('announcement').textContent = `Suggestion de souvenir : ${fact}`;
    $('conversationBody').scrollTop = $('conversationBody').scrollHeight;
    return card;
}
async function newSession() {
    const session = await (await api('/api/sessions', { method: 'POST' })).json();
    await refreshSessions();
    await loadSession(session.id);
}
async function refreshMemory() {
    const data = await (await api('/api/memory')).json();
    $('facts').replaceChildren();
    if (!data.facts.length) {
        const li = document.createElement('li'); li.textContent = 'Aucun souvenir enregistré.'; $('facts').appendChild(li);
    }
    for (const fact of data.facts) {
        const li = document.createElement('li');
        const span = document.createElement('span'); span.textContent = fact; li.appendChild(span);
        const button = document.createElement('button'); button.textContent = 'Oublier';
        button.setAttribute('aria-label', `Oublier : ${fact}`);
        button.addEventListener('click', () => action(async () => {
            await api('/api/memory', { method: 'DELETE', body: JSON.stringify({ fact }) });
            await refreshMemory();
        }));
        li.appendChild(button); $('facts').appendChild(li);
    }
}
async function action(fn) {
    if (busy || !online) return;
    setBusy(true);
    try { await fn(); } catch (err) { error(err.message); }
    finally { setBusy(false); }
}
async function sendMessage(text = $('userInput').value.trim(), { replaceLast = false } = {}) {
    if (!text || busy || !online) return;
    if (replaceLast && (!last || isCommand(text))) {
        error(isCommand(text) ? 'Un message modifié ne peut pas être une commande.' : 'Aucun échange à remplacer.');
        return;
    }
    // The server keeps the old exchange until the new one is saved; only the display changes now.
    const replaced = replaceLast ? last.elements : [];
    replaced.forEach(element => element.remove());
    last = null; editing = false;
    controller = new AbortController();
    setBusy(true);
    const userBubble = appendMessage(text, 'user', 'pending');
    const reply = appendMessage('', 'airi');
    $('userInput').value = '';
    let done = false, suggestion = null;
    try {
        const response = await api('/api/chat/stream', {
            method: 'POST', signal: controller.signal,
            body: JSON.stringify({ message: text, session_id: sessionId, replace_last: replaceLast }),
        });
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        try {
            while (true) {
                const part = await reader.read();
                buffer += decoder.decode(part.value, { stream: !part.done });
                let newline;
                while ((newline = buffer.indexOf('\n')) !== -1) {
                    const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1);
                    if (!line.trim()) continue;
                    const event = JSON.parse(line);
                    if (event.type === 'error') throw new Error(event.message);
                    if (event.type === 'delta') {
                        reply.textContent += event.text;
                        $('conversationBody').scrollTop = $('conversationBody').scrollHeight;
                    }
                    if (event.type === 'suggestion') suggestion = event.fact;
                    if (event.type === 'done') done = true;
                }
                if (part.done) break;
            }
        } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
        if (!done) throw new Error('Connexion interrompue : la réponse complète n’a pas été confirmée. Recharge la conversation avant de réessayer.');
        userBubble.classList.remove('pending');
        $('announcement').textContent = `AIRI : ${reply.textContent}`;
        const card = suggestion ? showSuggestion(suggestion) : null;
        if (!isCommand(text)) last = { text, elements: [userBubble, reply, ...(card ? [card] : [])] };
        // Errors refreshing metadata must not label an already-saved answer as failed.
        try {
            await refreshSessions();
            if (!$('memoryPanel').hidden) await refreshMemory();
        } catch (err) { error(`Réponse enregistrée. Actualisation impossible : ${err.message}`); }
    } catch (err) {
        if (controller.signal.aborted) {
            // Stopped on purpose: the server saves what was generated, marked as interrupted.
            // The server handles requests in order, so this reload shows what it really kept.
            userBubble.classList.remove('pending');
            reply.classList.add('interrupted');
            last = { text, elements: [userBubble, reply] };
            try { await loadSession(sessionId); await refreshSessions(); } catch (_) { /* Display kept as is */ }
            $('announcement').textContent = 'Réponse arrêtée.';
        } else if (replaceLast) {
            // Resynchronise with what the server actually kept, then explain.
            try { await loadSession(sessionId); } catch (_) { /* Reported below */ }
            error(`${err.message}\nL'échange précédent a été conservé.`);
            $('userInput').value = text;
        } else {
            reply.classList.add('error');
            reply.textContent += `\n${err.message}\nCet échange peut être incomplet ; vérifie l'historique avant de le renvoyer.`;
            $('announcement').textContent = err.message;
            $('userInput').value = text;
        }
    } finally { controller = null; setBusy(false); $('userInput').focus(); }
}
function stopReply() {
    if (controller) controller.abort();
}
function startEditing() {
    if (!last || busy || !online) return;
    editing = true;
    $('userInput').value = last.text;
    renderControls();
    $('userInput').focus();
}
function cancelEditing() {
    editing = false;
    $('userInput').value = '';
    renderControls();
}
$('chatForm').addEventListener('submit', event => { event.preventDefault(); sendMessage(undefined, { replaceLast: editing }); });
$('userInput').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(undefined, { replaceLast: editing }); }
    if (event.key === 'Escape' && editing) { event.preventDefault(); cancelEditing(); }
});
$('stopBtn').addEventListener('click', stopReply);
$('regenBtn').addEventListener('click', () => { if (last) sendMessage(last.text, { replaceLast: true }); });
$('editBtn').addEventListener('click', startEditing);
$('cancelEditBtn').addEventListener('click', cancelEditing);

// Conversations: rename, delete (two clicks), search.
function currentTitle() {
    const option = [...$('sessions').children].find(item => item.value === sessionId);
    return option ? option.textContent : '';
}
$('renameBtn').addEventListener('click', () => {
    if (busy || !online) return;
    $('renameForm').hidden = false;
    $('renameInput').value = currentTitle();
    $('renameInput').focus();
});
$('renameCancel').addEventListener('click', () => { $('renameForm').hidden = true; });
$('renameForm').addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
        await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'PATCH', body: JSON.stringify({ title: $('renameInput').value.trim() }) });
        $('renameForm').hidden = true;
        await refreshSessions();
        $('announcement').textContent = 'Conversation renommée.';
    });
});
let deleteTimer = null;
function disarmDelete() {
    clearTimeout(deleteTimer); deleteTimer = null;
    $('deleteBtn').textContent = 'Supprimer';
    $('deleteBtn').classList.remove('confirming');
}
$('deleteBtn').addEventListener('click', () => {
    if (busy || !online) return;
    if (!deleteTimer) {
        $('deleteBtn').textContent = 'Confirmer la suppression';
        $('deleteBtn').classList.add('confirming');
        $('announcement').textContent = 'Clique encore pour supprimer définitivement cette conversation.';
        deleteTimer = setTimeout(disarmDelete, 5000);
        return;
    }
    disarmDelete();
    action(async () => {
        await api(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
        const sessions = (await refreshSessions()).filter(session => session.updated_at);
        if (sessions.length) await loadSession(sessions[0].id);
        else await newSession();
        $('announcement').textContent = 'Conversation supprimée.';
    });
});
let searchTimer = null, searchQuery = '';
function clearSearch() {
    $('searchInput').value = ''; searchQuery = '';
    $('searchResults').replaceChildren(); $('searchResults').hidden = true;
}
async function runSearch(query) {
    searchQuery = query;
    if (query.length < 2) { $('searchResults').replaceChildren(); $('searchResults').hidden = true; return; }
    let results;
    try { results = await (await api(`/api/sessions/search?q=${encodeURIComponent(query)}`)).json(); }
    catch (err) { results = err; }
    if (query !== searchQuery) return;  // A newer search superseded this one.
    $('searchResults').replaceChildren();
    $('searchResults').hidden = false;
    if (results instanceof Error || !results.length) {
        const li = document.createElement('li'); li.className = 'empty';
        li.textContent = results instanceof Error ? results.message : 'Aucun résultat.';
        $('searchResults').appendChild(li);
        return;
    }
    for (const result of results) {
        const li = document.createElement('li');
        const button = document.createElement('button'); button.type = 'button';
        const title = document.createElement('strong'); title.textContent = result.title;
        const snippet = document.createElement('small'); snippet.textContent = result.snippet || `${result.matches} occurrence(s) dans le titre`;
        button.appendChild(title); button.appendChild(snippet);
        button.addEventListener('click', () => action(async () => { await loadSession(result.id); clearSearch(); }));
        li.appendChild(button); $('searchResults').appendChild(li);
    }
    $('announcement').textContent = `${results.length} conversation(s) trouvée(s).`;
}
$('searchInput').addEventListener('input', () => {
    clearTimeout(searchTimer);
    const query = $('searchInput').value.trim();
    searchTimer = setTimeout(() => runSearch(query), 250);
});
$('searchInput').addEventListener('keydown', event => { if (event.key === 'Escape') clearSearch(); });

// Whole-data export and import (validated server-side before anything is written).
function download(data, name) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a'); a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('exportAllBtn').addEventListener('click', () => action(async () => {
    $('settingsMenu').hidePopover?.();
    const data = await (await api('/api/data/export')).json();
    download(data, `airi-export-${data.exported_at.slice(0, 10)}.json`);
    $('announcement').textContent = data.skipped.length
        ? `Export terminé. Fichiers illisibles ignorés : ${data.skipped.join(', ')}` : 'Export terminé.';
}));
$('importBtn').addEventListener('click', () => { $('settingsMenu').hidePopover?.(); $('importFile').click(); });
function closeImport() {
    pendingImport = null; $('importPanel').hidden = true; $('importFile').value = '';
}
$('importFile').addEventListener('change', () => action(async () => {
    const file = $('importFile').files[0];
    if (!file) return;
    let bundle;
    try { bundle = JSON.parse(await file.text()); }
    catch (_) { closeImport(); throw new Error('Ce fichier n’est pas un JSON valide.'); }
    let preview;
    try { preview = await (await api('/api/data/import', { method: 'POST', body: JSON.stringify({ bundle, dry_run: true }) })).json(); }
    catch (err) { closeImport(); throw err; }
    pendingImport = bundle;
    const profile = preview.profile_files.length;
    $('importSummary').textContent = `${file.name} : ${preview.sessions_new} nouvelle(s) conversation(s), `
        + `${preview.sessions_existing} déjà présente(s) (conservées telles quelles), ${preview.facts_new} nouveau(x) souvenir(s).`
        + (profile ? ` Il contient aussi ton profil (${profile} fichiers) : « Tout importer » remplacera le profil actuel, une copie .bak est gardée.` : '');
    $('importConfirm').hidden = false;
    $('importAll').hidden = !profile;
    $('importPanel').hidden = false;
}));
async function confirmImport(includeProfile) {
    if (!pendingImport) return;
    const report = await (await api('/api/data/import', {
        method: 'POST', body: JSON.stringify({ bundle: pendingImport, include_profile: includeProfile }),
    })).json();
    pendingImport = null; $('importFile').value = '';
    $('importConfirm').hidden = $('importAll').hidden = true;
    $('importSummary').textContent = `Import terminé : ${report.sessions_added} conversation(s) ajoutée(s), `
        + `${report.facts_added} souvenir(s) ajouté(s)`
        + (report.facts_over_limit ? `, ${report.facts_over_limit} ignoré(s) car la mémoire est pleine` : '')
        + (report.profile_files.length ? ', profil remplacé.' : '.');
    $('announcement').textContent = $('importSummary').textContent;
    await refreshSessions();
    if (!$('memoryPanel').hidden) await refreshMemory();
}
$('importConfirm').addEventListener('click', () => action(() => confirmImport(false)));
$('importAll').addEventListener('click', () => action(() => confirmImport(true)));
$('importCancel').addEventListener('click', closeImport);
$('newBtn').addEventListener('click', () => action(newSession));
$('sessions').addEventListener('change', () => action(async () => {
    try { await loadSession($('sessions').value); }
    catch (err) { $('sessions').value = sessionId; throw err; }
}));
$('helpBtn').addEventListener('click', () => sendMessage('/aide'));
for (const [id, prompt] of [
    ['ideaBtn', 'J’ai une idée que j’aimerais explorer avec toi.'],
    ['projectBtn', 'Aide-moi à avancer sur mon projet.'],
    ['talkBtn', 'J’aimerais prendre un moment pour discuter avec toi.'],
]) {
    $(id).addEventListener('click', () => {
        if (busy || !online) return;
        $('userInput').value = prompt;
        $('userInput').focus();
    });
}
$('memoryBtn').addEventListener('click', () => action(async () => {
    $('memoryPanel').hidden = !$('memoryPanel').hidden;
    $('memoryBtn').setAttribute('aria-expanded', String(!$('memoryPanel').hidden));
    if (!$('memoryPanel').hidden) await refreshMemory();
}));
$('memoryForm').addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
        await api('/api/memory', { method: 'POST', body: JSON.stringify({ fact: $('newFact').value.trim() }) });
        $('newFact').value = ''; await refreshMemory();
    });
});
$('exportBtn').addEventListener('click', () => action(async () => {
    const data = await (await api(`/api/sessions/${encodeURIComponent(sessionId)}`)).json();
    download(data, `airi-${sessionId}.json`);
}));
async function boot() {
    try {
        const bootstrap = await (await api('/api/bootstrap')).json();
        token = bootstrap.token; $('userInput').maxLength = bootstrap.max_message_chars;
        // The user's name comes from the profile (data/user_profile.json), never from the code.
        $('welcomeName').textContent = bootstrap.user_name || '';
        $('welcomeGreeting').hidden = !bootstrap.user_name;
        const sessions = await refreshSessions();
        let saved = null;
        try {
            saved = globalThis.airiDesktop ? await globalThis.airiDesktop.readSession() : localStorage.getItem('airi.session');
        } catch (_) { /* Optional preference */ }
        const healthy = sessions.filter(session => session.updated_at);
        if (saved && healthy.some(s => s.id === saved)) await loadSession(saved);
        else if (healthy.length) await loadSession(healthy[0].id);
        else await newSession();
        online = true;
        lastReady = true;
    } catch (err) {
        online = false;
        error(`Connexion impossible : ${err.message}. Recharge la page après avoir vérifié le serveur.`);
    } finally { renderControls(); }
}
setInterval(async () => {
    if (busy) return;
    try {
        const data = await (await api('/api/health', { signal: AbortSignal.timeout(3000) })).json();
        online = Boolean(data.ready) && lastReady;
    } catch (_) { online = false; }
    renderControls();
}, 10000);
boot();
