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
const port = 9340;
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
    await ui.click('.settings-trigger');
    await ui.capture('AIRI-Mini-0.1.6-debug.png');
    const state = `(() => {
      const field = document.querySelector('#themeChoices');
      return {
        theme: document.documentElement.dataset.theme, app: !!globalThis.airiSettings,
        resources: performance.getEntriesByType('resource').map(entry => entry.name),
        desktopControls: document.querySelector('[data-desktop]').hidden,
        current: field.querySelector(':checked')?.value,
        appearance: getComputedStyle(document.querySelector('.menu-sheet')).backgroundColor,
        labelBounds: field.querySelector('label:has(input[value="dark"])').getBoundingClientRect().toJSON()
      }
    })()`;
    console.log(await ui.js(state));
    await ui.click('label:has(input[value="dark"])');
    await delay(250);
    console.log(await ui.js(state), await ui.js('airiSettings.readTheme()'));
    await ui.js('document.querySelector("input[value=dark]").click()');
    await delay(250);
    console.log(await ui.js(state), await ui.js('airiSettings.readTheme()'));
    await ui.close();
}

main().catch(async error => {
    console.error(error);
    try { console.error((await fs.readFile(log, 'utf8')).slice(-6000)); } catch {}
    client?.close();
    child?.kill();
    process.exitCode = 1;
});
