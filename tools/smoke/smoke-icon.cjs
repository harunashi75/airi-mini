const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const root = process.cwd();
const binary = path.resolve(process.argv[2]);
const profile = path.join(root, '.desktop-build', 'smoke-linux-profile', String(process.pid));
const model = path.join(root, 'models', 'gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf');
const port = Number(process.env.AIRI_SMOKE_PORT) || 9333;
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
    const args = ['--disable-gpu', '--remote-debugging-port=' + port, '--user-data-dir=' + profile, '--ozone-platform=headless'];
    child = spawn(binary, args, { env: { ...process.env, APPIMAGE_EXTRACT_AND_RUN: '1' }, stdio: 'pipe' });
    child.stderr.on('data', buffer => process.stderr.write(buffer));
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
    await until('Icône du démarrage absente', () => ui.js('document.querySelector(\'img[src="icon.png"]\')?.naturalWidth === 512'));
    await ui.close();
    const configFile = path.join(profile, 'airi', 'config', 'airi.json');
    const config = JSON.parse(await fs.readFile(configFile, 'utf8'));
    config.model_path = model;
    config.max_tokens = 8;
    await fs.writeFile(configFile, JSON.stringify(config));
    ui = await launch();
    await until('Chat non connecté', () => ui.js('document.getElementById("statusText")?.textContent === "En ligne"'), 80000);
    const logos = await ui.js(`Array.from(document.querySelectorAll('img[src="/assets/logo.svg"]'), icon => ({
        loaded: icon.complete && icon.naturalWidth > 0, path: new URL(icon.src).pathname
    }))`);
    assert.equal(logos.length, 3);
    assert.ok(logos.every(logo => logo.loaded && logo.path === '/assets/logo.svg'));
    assert.equal(await ui.js('document.querySelector(\'link[rel="icon"]\').getAttribute("href")'), '/assets/logo.svg');
    const servedLogo = await ui.js('fetch("/assets/logo.svg").then(response => response.text())');
    assert.equal(servedLogo, await fs.readFile(path.join(root, 'desktop', 'logo.svg'), 'utf8'));
    await ui.close();
    console.log('Nouvelle icône chargée dans la fenêtre de démarrage, le chat et le favicon');
}

main().catch(async error => {
    console.error(error);
    try { console.error((await fs.readFile(log, 'utf8')).slice(-6000)); } catch {}
    client?.close();
    child?.kill();
    process.exitCode = 1;
});
