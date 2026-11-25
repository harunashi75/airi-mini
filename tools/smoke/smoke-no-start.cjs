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
        async click(selector) {
            const point = await this.js(`(() => {
                const rect = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();
                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
            })()`);
            await command('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
            await command('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
        },
        async key(key, virtualKey) {
            await command('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, windowsVirtualKeyCode: virtualKey });
            await command('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: virtualKey });
        },
        async layout(width, height) {
            await command('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
            await delay(180);
        },
        async capture(name) {
            const shot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
            await fs.writeFile(path.join(root, 'dist', name), Buffer.from(shot.result.data, 'base64'));
        },
        async js(expression) {
            const reply = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true });
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
    await ui.click('.settings-trigger');
    await until('Paramètres absent au démarrage', () => ui.js('document.getElementById("settingsMenu").matches(":popover-open")'));
    await ui.click('label:has(input[value="dark"])');
    await until('Thème sombre non appliqué', () => ui.js('document.documentElement.dataset.theme === "dark" && airiSettings.readTheme().then(theme => theme === "dark")'));
    await ui.key('Escape', 27);
    await until('Échap ne ferme pas le menu', () => ui.js('!document.getElementById("settingsMenu").matches(":popover-open")'));
    await ui.close();

    const configFile = path.join(profile, 'airi', 'config', 'airi.json');
    const config = JSON.parse(await fs.readFile(configFile, 'utf8'));
    config.model_path = model;
    config.max_tokens = 8;
    await fs.writeFile(configFile, JSON.stringify(config));
    ui = await launch();
    await until('Chat non connecté', () => ui.js('document.getElementById("statusText")?.textContent === "En ligne"'), 80000);
    assert.equal(await ui.js('document.documentElement.dataset.theme'), 'dark');
    assert.equal(await ui.js('document.querySelectorAll("[popovertarget=startMenu],#startMenu,.start-trigger").length'), 0);
    assert.equal(await ui.js('typeof airiSettings.quit'), 'undefined');
    const first = await ui.js('document.getElementById("sessions").value');
    await ui.click('#newBtn');
    await until('Nouvelle conversation absente', () => ui.js('document.getElementById("sessions").options.length === 2 && !document.getElementById("sessions").disabled'));
    await ui.click('#sessions');
    await until('Sélecteur de conversations absent', () => ui.js('document.getElementById("sessions").matches(":open")'));
    await ui.key('ArrowDown', 40);
    await ui.key('Enter', 13);
    await until('La conversation choisie ne s’ouvre pas', () => ui.js('!document.getElementById("memoryBtn").disabled && document.getElementById("sessions").value === ' + JSON.stringify(first)));
    await ui.click('#memoryBtn');
    await until('Panneau Souvenirs absent', () => ui.js('!document.getElementById("memoryPanel").hidden'));
    await ui.click('.settings-trigger');
    await until('Paramètres absent du chat', () => ui.js('document.getElementById("settingsMenu").matches(":popover-open")'));
    await ui.close();
    console.log('Menu Démarrer retiré. Paramètres, thème mémorisé, sélection au clavier et souvenirs vérifiés.');
}

main().catch(async error => {
    console.error(error);
    try { console.error((await fs.readFile(log, 'utf8')).slice(-6000)); } catch {}
    client?.close();
    child?.kill();
    process.exitCode = 1;
});
