const syncFS = require('node:fs');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const root = process.cwd();
const binary = path.resolve(process.argv[2]);
const wine = process.argv[3] === 'wine';
const winPath = filename => 'Z:' + filename.replaceAll('/', '\\');
const profile = path.join(root, '.desktop-build', wine ? 'smoke-win-profile' : 'smoke-linux-profile', String(process.pid));
const model = path.join(root, 'models', 'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf');
const port = Number(process.env.AIRI_SMOKE_PORT) || (wine ? 9334 : 9333);
const log = path.join(profile, 'airi-desktop.log');
let child, client, endpoint;

async function until(description, check, timeout = 35000) {
    let lastError;
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        try {
            const result = await check();
            if (result) return result;
        } catch (error) { lastError = error; }
        await delay(150);
    }
    throw new Error(description, { cause: lastError });
}

async function launch() {
    endpoint = null;
    const common = ['--disable-gpu', '--remote-debugging-port=' + port, '--user-data-dir=' + (wine ? winPath(profile) : profile)];
    const args = wine ? [binary, ...common, '--no-sandbox'] : [...common, '--ozone-platform=headless'];
    const fd = wine ? syncFS.openSync(path.join(profile, 'wine.log'), 'a') : null;
    child = spawn(wine ? 'wine' : binary, args, { env: { ...process.env, APPIMAGE_EXTRACT_AND_RUN: '1' }, stdio: wine ? ['ignore', fd, fd] : 'pipe' });
    if (wine) syncFS.closeSync(fd);
    else child.stderr.on('data', buffer => process.stderr.write(buffer));
    const targets = await until('Fenêtre Electron introuvable', async () => {
        const response = await fetch('http://127.0.0.1:' + port + '/json/list');
        const pages = await response.json();
        return pages.find(page => page.type === 'page');
    });
    const ws = new WebSocket(targets.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
        ws.addEventListener('open', resolve, { once: true });
        ws.addEventListener('error', reject, { once: true });
    });
    client = ws;
    const pending = new Map();
    let nextId = 0;
    ws.addEventListener('message', event => {
        const response = JSON.parse(event.data);
        const resolve = pending.get(response.id);
        if (resolve) { pending.delete(response.id); resolve(response); }
    });
    const command = async (method, params) => {
        const id = ++nextId;
        const reply = new Promise(resolve => pending.set(id, resolve));
        ws.send(JSON.stringify({ id, method, params }));
        return reply;
    };
    return {
        async js(expression) {
            const reply = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
            if (reply.result.exceptionDetails) throw new Error(reply.result.exceptionDetails.exception.description);
            return reply.result.result.value;
        },
        async close() {
            const exit = new Promise(resolve => child.once('exit', resolve));
            ws.send(JSON.stringify({ id: ++nextId, method: 'Page.close' }));
            await Promise.race([exit, delay(15000).then(() => { throw new Error('Electron ne ferme pas'); })]);
            ws.close();
        }
    };
}

async function main() {
    await fs.mkdir(profile, { recursive: true });
    let ui = await launch();
    await until('Sélecteur GGUF absent au premier démarrage', () => ui.js('document.getElementById("title")?.textContent === "Choisis ton modèle GGUF"'));
    console.log('Premier démarrage et sélecteur GGUF OK');
    await ui.close();
    const configFile = path.join(profile, 'airi', 'config', 'airi.json');
    const config = JSON.parse(await fs.readFile(configFile, 'utf8'));
    config.model_path = wine ? winPath(model) : model;
    config.max_tokens = 8;
    await fs.writeFile(configFile, JSON.stringify(config));
    ui = await launch();
    await until('Chat non connecté', () => ui.js('document.getElementById("statusText")?.textContent === "En ligne"'), 80000);
    endpoint = await ui.js('location.origin');
    assert.equal((await fetch(endpoint + '/api/bootstrap')).status, 403);
    const first = await ui.js('document.getElementById("sessions").value');
    assert.match(first, /^[a-f0-9-]{36}$/);
    await ui.js('document.getElementById("userInput").value = "Bonjour !"; document.getElementById("sendBtn").click()');
    await until('Réponse et sauvegarde absentes', () => ui.js('document.querySelectorAll(".message.airi:not(.error)").length > 0 && !document.getElementById("sendBtn").disabled'), 45000);
    await ui.js('document.getElementById("newBtn").click()');
    await until('Nouvelle conversation absente', () => ui.js('document.getElementById("sessions").options.length === 2 && !document.getElementById("newBtn").disabled'));
    await ui.js('(() => { const select=document.getElementById("sessions"); select.value=' + JSON.stringify(first) + '; select.dispatchEvent(new Event("change")); })()');
    await until('Conversation initiale absente', () => ui.js('document.querySelector(".message.user")?.textContent === "Bonjour !"'));
    await ui.close();
    await until('Moteur toujours actif après fermeture', async () => {
        try { await fetch(endpoint + '/api/health'); return false; } catch { return true; }
    });
    console.log('Inférence réelle, API réservée à Electron, conversation enregistrée et arrêt du moteur OK');
    ui = await launch();
    await until('Reprise de la dernière conversation absente', () => ui.js('document.querySelector(".message.user")?.textContent === "Bonjour !"'), 80000);
    await ui.close();
    console.log('Dernière conversation restaurée après redémarrage OK');
}

main().catch(async error => {
    console.error(error);
    try { console.error((await fs.readFile(log, 'utf8')).slice(-6000)); } catch {}
    client?.close();
    child?.kill();
    process.exitCode = 1;
});
