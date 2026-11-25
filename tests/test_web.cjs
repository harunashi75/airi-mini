// Exercise the UI controller without downloading a browser or dependencies.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

class Element {
    constructor() {
        this.children = []; this.value = ''; this.textContent = ''; this.hidden = false;
        this.disabled = false; this.style = {}; this.events = {};
        this.classes = new Set();
        this.classList = { add: x => this.classes.add(x), remove: x => this.classes.delete(x), toggle: (x, on) => on ? this.classes.add(x) : this.classes.delete(x) };
    }
    appendChild(child) { this.children.push(child); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, handler) { this.events[name] = handler; }
    setAttribute(name, value) { this[name] = value; }
    querySelectorAll() { return []; }
    focus() {}
    remove() { this.removed = true; }
}
async function harness(events, { corrupt = false, healthResponse, replyReady, messages = [], routes = {} } = {}) {
    const elements = new Map();
    const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
    get('memoryPanel').hidden = true;
    let requests = 0;
    let checkHealth;
    const session = { id: 'healthy', title: 'Test', updated_at: '2026-11-05', messages };
    const bodies = [];
    const context = vm.createContext({
        document: { getElementById: get, createElement: () => new Element() },
        localStorage: { getItem: () => corrupt ? 'broken' : null, setItem() {} },
        TextDecoder, TextEncoder, AbortSignal, AbortController, Blob, URL,
        setInterval(callback) { checkHealth = callback; }, setTimeout, clearTimeout,
        fetch: async (url, options) => {
            const result = data => ({ ok: true, json: async () => data });
            const routed = routes[url] && await routes[url](options);
            if (routed !== undefined && routed !== false) { bodies.push({ url, method: options?.method, body: options?.body && JSON.parse(options.body) }); return result(routed); }
            if (url === '/api/bootstrap') return result({ token: 'test', max_message_chars: 8000 });
            if (url === '/api/health') return result(await healthResponse);
            if (url === '/api/sessions') return result(corrupt ? [{ id: 'broken', title: 'Illisible', updated_at: '' }, session] : [session]);
            if (url === '/api/sessions/healthy') return result(session);
            if (url === '/api/chat/stream') {
                requests++;
                bodies.push({ url, body: JSON.parse(options.body) });
                assert.equal(JSON.parse(options.body).session_id, 'healthy');
                // Split every byte, including multibyte Unicode and NDJSON delimiters.
                const bytes = new TextEncoder().encode(events.map(event => JSON.stringify(event)).join('\n') + '\n');
                let offset = 0;
                return { ok: true, body: { getReader: () => ({
                    read: async () => {
                        await replyReady;
                        if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
                        return offset < bytes.length ? { done: false, value: bytes.slice(offset, ++offset) } : { done: true };
                    },
                    cancel: async () => {}, releaseLock() {},
                }) } };
            }
            throw new Error('Unexpected URL ' + url);
        },
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(vm.runInContext('online', context), true);
    return { context, get, checkHealth, session, bodies, requests: () => requests };
}

test('Streaming preserves Unicode across byte boundaries and prevents duplicate sends', async () => {
    const h = await harness([{ type: 'delta', text: 'Bonjour 👋' }, { type: 'done' }]);
    const first = vm.runInContext("sendMessage('salut')", h.context);
    await vm.runInContext("sendMessage('doublon')", h.context);
    await first;
    assert.equal(h.requests(), 1);
    assert.equal(h.get('messagesArea').children.at(-1).textContent, 'Bonjour 👋');
    assert.equal(h.get('sendBtn').disabled, false);
    assert.equal(h.get('userInput').value, '');
});

for (const ready of [true, false]) {
    test(`A delayed health check (ready=${ready}) leaves the active send locked`, async () => {
        const health = Promise.withResolvers();
        const reply = Promise.withResolvers();
        const h = await harness([{ type: 'delta', text: 'Bonjour' }, { type: 'done' }], {
            healthResponse: health.promise,
            replyReady: reply.promise,
        });
        const checking = h.checkHealth();
        const sending = vm.runInContext("sendMessage('premier')", h.context);
        let duplicate;

        try {
            assert.equal(h.requests(), 1);
            assert.equal(h.get('sendBtn').disabled, true);
            health.resolve({ ready });
            await checking;

            assert.equal(h.get('sendBtn').disabled, true);
            assert.equal(h.get('userInput').disabled, true);
            assert.equal(h.get('statusText').textContent, 'AIRI réfléchit…');
            duplicate = vm.runInContext("sendMessage('doublon')", h.context);
            assert.equal(h.requests(), 1);
        } finally {
            health.resolve({ ready });
            reply.resolve();
            await Promise.all([checking, sending, duplicate]);
        }

        assert.equal(h.get('sendBtn').disabled, !ready);
        assert.equal(h.get('statusText').textContent, ready ? 'En ligne' : 'Serveur indisponible');
    });
}

test('Failed stream preserves the user input and reports incomplete response', async () => {
    const h = await harness([{ type: 'delta', text: 'Début' }, { type: 'error', message: 'Erreur de génération' }]);
    await vm.runInContext("sendMessage('à réessayer')", h.context);
    assert.equal(h.get('userInput').value, 'à réessayer');
    assert.match(h.get('messagesArea').children.at(-1).textContent, /Erreur de génération/);
    assert.equal(h.get('sendBtn').disabled, false);
});

test('A truncated stream is not presented as a saved reply', async () => {
    const h = await harness([{ type: 'delta', text: 'Incomplet' }]);
    await vm.runInContext("sendMessage('test')", h.context);
    assert.match(h.get('messagesArea').children.at(-1).textContent, /Connexion interrompue/);
});

test('A memory suggestion is only saved after the user confirms it', async () => {
    const h = await harness([{ type: 'delta', text: 'Noté.' }, { type: 'suggestion', fact: 'Haru aime le thé' }, { type: 'done' }]);
    const posted = [];
    const fetchBefore = h.context.fetch;
    h.context.fetch = async (url, options) => {
        if (url === '/api/memory') { posted.push(JSON.parse(options.body)); return { ok: true, json: async () => ({}) }; }
        return fetchBefore(url, options);
    };
    await vm.runInContext("sendMessage('J\\'aime le thé')", h.context);
    const card = h.get('messagesArea').children.at(-1);
    assert.equal(card.className, 'memory-suggestion');
    assert.match(card.children[0].textContent, /Haru aime le thé/);
    assert.deepEqual(posted, []);
    await card.children[1].events.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(posted, [{ fact: 'Haru aime le thé' }]);
    assert.match(card.children[0].textContent, /Souvenir enregistré/);
});

const exchange = [
    { role: 'user', content: 'question', kind: 'chat', interrupted: false },
    { role: 'assistant', content: 'réponse', kind: 'chat', interrupted: false },
];

test('Regenerate replaces the last exchange and edit sends the corrected text', async () => {
    const h = await harness([{ type: 'delta', text: 'Nouvelle' }, { type: 'done' }], { messages: [...exchange] });
    assert.equal(h.get('regenBtn').hidden, false);
    const [oldQuestion, oldAnswer] = h.get('messagesArea').children;
    await h.get('regenBtn').events.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(h.bodies.at(-1).body, { message: 'question', session_id: 'healthy', replace_last: true });
    assert.equal(oldQuestion.removed && oldAnswer.removed, true);

    h.get('editBtn').events.click();
    assert.equal(h.get('userInput').value, 'question');
    assert.equal(h.get('editBanner').hidden, false);
    h.get('userInput').value = 'question corrigée';
    await h.get('chatForm').events.submit({ preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(h.bodies.at(-1).body, { message: 'question corrigée', session_id: 'healthy', replace_last: true });
    assert.equal(h.get('editBanner').hidden, true);
});

test('No regenerate after a command exchange', async () => {
    const h = await harness([], { messages: exchange.map(m => ({ ...m, kind: 'command' })) });
    assert.equal(h.get('regenBtn').hidden, true);
    assert.equal(h.get('editBtn').hidden, true);
});

test('Stop aborts the stream and shows the reply as interrupted', async () => {
    const reply = Promise.withResolvers();
    const h = await harness([{ type: 'delta', text: 'Début' }, { type: 'done' }], { replyReady: reply.promise });
    const sending = vm.runInContext("sendMessage('long')", h.context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.get('stopBtn').hidden, false);
    assert.equal(h.get('sendBtn').hidden, true);
    h.get('stopBtn').events.click();
    reply.resolve();
    await sending;
    assert.equal(h.get('announcement').textContent, 'Réponse arrêtée.');
    assert.equal(h.get('stopBtn').hidden, true);
    assert.equal(h.get('userInput').value, '');
});

test('Rename, two-step delete and search call the conversation routes', async () => {
    const h = await harness([], { routes: {
        '/api/sessions/healthy': options => Boolean(options?.method) && { ok: true },
        '/api/sessions/search?q=jazz': () => [{ id: 'healthy', title: 'Musique', snippet: '…le jazz…', matches: 1 }],
    } });
    h.get('renameBtn').events.click();
    h.get('renameInput').value = 'Nouveau titre';
    await h.get('renameForm').events.submit({ preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(h.bodies.find(b => b.method === 'PATCH').body, { title: 'Nouveau titre' });

    h.get('deleteBtn').events.click();
    assert.equal(h.bodies.some(b => b.method === 'DELETE'), false);
    assert.equal(h.get('deleteBtn').textContent, 'Confirmer la suppression');
    h.get('deleteBtn').events.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.bodies.some(b => b.method === 'DELETE'), true);

    await vm.runInContext("runSearch('jazz')", h.context);
    const [item] = h.get('searchResults').children;
    assert.equal(item.children[0].children[0].textContent, 'Musique');
    assert.equal(h.get('searchResults').hidden, false);
});

test('Import previews first and only writes after confirmation', async () => {
    const calls = [];
    const h = await harness([], { routes: { '/api/data/import': options => {
        const body = JSON.parse(options.body); calls.push(body);
        return body.dry_run
            ? { sessions_new: 2, sessions_existing: 1, facts_new: 3, profile_files: ['data/identity.json'] }
            : { sessions_added: 2, facts_added: 3, facts_over_limit: 0, profile_files: [] };
    } } });
    h.get('importFile').files = [{ name: 'export.json', text: async () => JSON.stringify({ format: 'airi-mini-export' }) }];
    await h.get('importFile').events.change();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].dry_run, true);
    assert.match(h.get('importSummary').textContent, /2 nouvelle\(s\) conversation/);
    assert.equal(h.get('importAll').hidden, false);
    await h.get('importConfirm').events.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 2);
    assert.equal(calls[1].include_profile, false);
    assert.match(h.get('importSummary').textContent, /Import terminé/);
});

test('A corrupt last session does not prevent opening a healthy conversation', async () => {
    const h = await harness([], { corrupt: true });
    assert.equal(vm.runInContext('sessionId', h.context), 'healthy');
});
