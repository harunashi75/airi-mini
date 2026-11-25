'use strict';

const { app, BrowserWindow, dialog, ipcMain, Menu, nativeTheme, shell } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { pathToFileURL } = require('node:url');

const project = path.join(__dirname, '..');
const windows = process.platform === 'win32';
const assets = app.isPackaged ? process.resourcesPath : project;
const setupFile = path.join(assets, 'desktop', 'setup.html');
const setupURL = pathToFileURL(setupFile).href;
const DATA_DIRECTORY_BUSY_EXIT = 73; // Reported by desktop_backend.py.
const themes = ['system', 'light', 'dark'];
let window, backend, origin, home, configPath, sessionPath, logPath, themePath;
let stopping;
let changingModel = false;
let quitting = false;

function copyDefaultConfig() {
    if (!app.isPackaged) return;
    const defaults = path.join(assets, 'config');
    const destination = path.join(home, 'config');
    fs.mkdirSync(destination, { recursive: true, mode: 0o700 });
    for (const name of ['airi.json', 'personality.json']) {
        try {
            const filename = path.join(destination, name);
            fs.copyFileSync(path.join(defaults, name), filename, fs.constants.COPYFILE_EXCL);
            fs.chmodSync(filename, 0o600);
        } catch (error) {
            if (error.code !== 'EEXIST') throw error;
        }
    }
}

function readConfig() {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
        throw new Error('config/airi.json doit contenir un objet JSON.');
    }
    return config;
}

function saveFile(destination, value) {
    const temporary = `${destination}.${randomBytes(8).toString('hex')}.tmp`;
    try {
        fs.writeFileSync(temporary, value, { flag: 'wx', mode: 0o600 });
        fs.renameSync(temporary, destination);
    } finally {
        fs.rmSync(temporary, { force: true });
    }
}

function writeLog(data) {
    try {
        fs.appendFileSync(logPath, data, { mode: 0o600 });
    } catch (error) {
        console.error(error.message);
    }
}

async function showSetup(title, message, loading = false) {
    if (quitting || window.isDestroyed()) return;
    if (window.webContents.getURL() !== setupURL) await window.loadFile(setupFile);
    if (!quitting && !window.isDestroyed()) {
        window.webContents.send('setup:state', { title, message, loading });
    }
}

function backendCommand() {
    if (app.isPackaged) {
        return [path.join(assets, 'backend', windows ? 'airi-backend.exe' : 'airi-backend'), []];
    }
    const python = path.join(project, 'venv', windows ? 'Scripts' : 'bin', windows ? 'python.exe' : 'python');
    return [process.env.AIRIMINI_PYTHON || (fs.existsSync(python) ? python : windows ? 'python' : 'python3'),
        ['-u', path.join(project, 'desktop_backend.py')]];
}

async function startBackend() {
    if (backend || quitting) return;
    copyDefaultConfig();
    fs.mkdirSync(path.join(home, 'data'), { recursive: true, mode: 0o700 });
    const modelPath = readConfig().model_path;
    if (typeof modelPath !== 'string' || !modelPath || !fs.statSync(path.resolve(home, modelPath), { throwIfNoEntry: false })?.isFile()) {
        await showSetup('Choisis ton modèle GGUF', 'Sélectionne un fichier GGUF compatible sur cet appareil pour démarrer AIRI.');
        return;
    }
    await showSetup('Chargement du modèle', path.basename(modelPath), true);
    if (quitting) return;

    const [command, args] = backendCommand();
    const token = randomBytes(32).toString('hex');
    const child = spawn(command, args, {
        cwd: home,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', AIRIMINI_HOME: home, AIRIMINI_ASSETS: assets,
            AIRIMINI_DESKTOP_TOKEN: token },
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true
    });
    const processState = { child, token, closed: new Promise(resolve => child.once('close', resolve)) };
    backend = processState;
    child.stdin.on('error', () => {}); // The backend can exit before its stdin is closed.
    child.stderr.on('data', writeLog);
    child.on('error', error => writeLog(`${error.message}\n`));
    createInterface({ input: child.stdout }).on('line', line => {
        const ready = /^AIRIMINI_READY (\d+)$/.exec(line);
        if (ready && backend === processState && !origin && !quitting) {
            origin = `http://127.0.0.1:${ready[1]}`;
            window.loadURL(origin).catch(error => {
                if (backend === processState && !quitting) {
                    runAction(async () => {
                        await stopBackend();
                        throw error;
                    });
                }
            });
        } else {
            writeLog(`${line}\n`);
        }
    });
    processState.closed.then(code => {
        writeLog(`Moteur arrêté (code : ${code}).\n`);
        if (backend !== processState || quitting) return;
        backend = null;
        origin = null;
        const message = code === DATA_DIRECTORY_BUSY_EXIT
            ? ['Dossier de données déjà utilisé', 'Ferme l’autre instance d’AIRI qui utilise ce dossier, puis réessaie.']
            : ['AIRI a perdu la connexion au moteur', 'Consulte le journal dans Paramètres, puis réessaie ou choisis un autre modèle.'];
        showSetup(...message).catch(error => writeLog(`${error.message}\n`));
    });
}

async function stopBackend() {
    if (stopping) return stopping;
    const running = backend;
    backend = null;
    origin = null;
    if (running) {
        running.child.stdin.end();
        const deadline = setTimeout(() => running.child.kill(), 8000);
        stopping = running.closed;
        try {
            await stopping;
        } finally {
            clearTimeout(deadline);
            stopping = null;
        }
    }
}

async function runAction(action) {
    if (changingModel || quitting) return;
    changingModel = true;
    try {
        await action();
    } catch (error) {
        writeLog(`${error.stack || error}\n`);
        await showSetup('Démarrage impossible', 'Consulte le journal ou la configuration dans le dossier de données, puis réessaie.');
    } finally {
        changingModel = false;
    }
}

function chooseModel() {
    return runAction(async () => {
        const { canceled, filePaths } = await dialog.showOpenDialog(window, {
            title: 'Choisir un modèle GGUF',
            properties: ['openFile'],
            filters: [{ name: 'Modèles GGUF', extensions: ['gguf'] }]
        });
        if (canceled || quitting) return;
        const filename = filePaths[0];
        if (path.extname(filename).toLowerCase() !== '.gguf' || !fs.statSync(filename).isFile()) {
            if (backend) {
                await dialog.showMessageBox(window, { type: 'error', message: 'Choisis un fichier GGUF valide.' });
            } else {
                await showSetup('Choisis ton modèle GGUF', 'Le fichier doit être un modèle GGUF valide.');
            }
            return;
        }
        const config = readConfig();
        config.model_path = filename;
        saveFile(configPath, JSON.stringify(config, null, 2) + '\n');
        await showSetup('Chargement du modèle', path.basename(filename), true);
        await stopBackend();
        await startBackend();
    });
}

function trustedFrame(event, url) {
    return url && event.sender === window.webContents && event.senderFrame === event.sender.mainFrame
        && event.senderFrame.url === url;
}

function windowBackground() {
    return nativeTheme.shouldUseDarkColors ? '#211827' : '#f7ecf4';
}

function installHandlers() {
    const contents = window.webContents;
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
    const restrictNavigation = event => {
        if (!event.isMainFrame || (event.url !== setupURL && event.url !== (origin && `${origin}/`))) {
            event.preventDefault();
        }
    };
    contents.on('will-frame-navigate', restrictNavigation);
    contents.on('will-redirect', restrictNavigation);
    const browserSession = contents.session;
    browserSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
    browserSession.setPermissionCheckHandler(() => false);
    browserSession.webRequest.onBeforeSendHeaders({ urls: ['http://127.0.0.1/*'] }, (details, callback) => {
        if (backend && origin && details.webContentsId === contents.id && new URL(details.url).origin === origin) {
            details.requestHeaders['X-Airi-Desktop'] = backend.token;
        }
        callback({ requestHeaders: details.requestHeaders });
    });
    browserSession.on('will-download', (event, item, source) => {
        if (source !== contents || !origin || !item.getURL().startsWith(`blob:${origin}/`)) {
            event.preventDefault();
            return;
        }
        item.setSaveDialogOptions({ title: 'Exporter la conversation', filters: [{ name: 'JSON', extensions: ['json'] }] });
    });
    ipcMain.handle('setup:choose', event => {
        if (trustedFrame(event, setupURL)) return chooseModel();
    });
    ipcMain.handle('setup:retry', event => {
        if (trustedFrame(event, setupURL)) return runAction(startBackend);
    });
    const settingsActions = {
        'read-theme': () => nativeTheme.themeSource,
        'save-theme': theme => {
            if (!themes.includes(theme)) throw new Error('Le thème demandé est invalide.');
            saveFile(themePath, theme);
            nativeTheme.themeSource = theme;
        },
        'choose-model': chooseModel,
        'open-data': () => openPath(home),
        'open-log': () => openPath(logPath)
    };
    for (const [name, action] of Object.entries(settingsActions)) {
        ipcMain.handle(`settings:${name}`, (event, ...args) => {
            if (trustedFrame(event, setupURL) || trustedFrame(event, origin && `${origin}/`)) {
                return action(...args);
            }
        });
    }
    ipcMain.handle('session:read', event => {
        if (trustedFrame(event, origin && `${origin}/`) && fs.existsSync(sessionPath)) {
            return fs.readFileSync(sessionPath, 'utf8');
        }
        return null;
    });
    ipcMain.handle('session:save', (event, id) => {
        if (trustedFrame(event, origin && `${origin}/`) && typeof id === 'string'
                && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)) {
            saveFile(sessionPath, id);
        }
    });
}

async function openPath(filename) {
    const error = await shell.openPath(filename);
    if (error && window && !window.isDestroyed()) {
        await dialog.showMessageBox(window, { type: 'error', message: 'Impossible d’ouvrir ce fichier ou dossier.', detail: error });
    }
}

async function start() {
    if (windows) app.setAppUserModelId('local.airi.mini');
    const userData = app.getPath('userData');
    home = app.isPackaged ? path.join(userData, 'airi') : project;
    configPath = path.join(home, 'config', 'airi.json');
    sessionPath = path.join(userData, app.isPackaged ? 'last-session' : 'last-session-dev');
    logPath = path.join(userData, 'airi-desktop.log');
    themePath = path.join(userData, 'theme');
    fs.mkdirSync(userData, { recursive: true, mode: 0o700 });
    fs.writeFileSync(logPath, '', { mode: 0o600 });
    if (fs.existsSync(themePath)) {
        try {
            const theme = fs.readFileSync(themePath, 'utf8').trim();
            if (themes.includes(theme)) nativeTheme.themeSource = theme;
        } catch (error) {
            writeLog(`Lecture de l’apparence impossible : ${error.message}\n`);
        }
    }

    window = new BrowserWindow({
        title: 'AIRI Mini', width: 1150, height: 800, minWidth: 480, minHeight: 560,
        backgroundColor: windowBackground(),
        icon: path.join(assets, 'desktop', 'icon.png'),
        webPreferences: {
            preload: path.join(__dirname, 'preload.cjs'),
            contextIsolation: true, nodeIntegration: false, sandbox: true, partition: 'airi-desktop'
        }
    });
    nativeTheme.on('updated', () => {
        if (!window.isDestroyed()) window.setBackgroundColor(windowBackground());
    });
    installHandlers();
    Menu.setApplicationMenu(Menu.buildFromTemplate([
        { label: 'Édition', submenu: [
            { role: 'undo', label: 'Annuler' }, { role: 'redo', label: 'Rétablir' },
            { type: 'separator' },
            { role: 'cut', label: 'Couper' }, { role: 'copy', label: 'Copier' },
            { role: 'paste', label: 'Coller' }, { role: 'selectAll', label: 'Tout sélectionner' }
        ] }
    ]));
    window.setMenuBarVisibility(false);
    await runAction(startBackend);
}

app.setName('AIRI Mini');
if (!app.requestSingleInstanceLock()) {
    app.quit();
} else {
    app.on('second-instance', () => {
        if (!window || window.isDestroyed()) return;
        if (window.isMinimized()) window.restore();
        window.focus();
    });
    app.on('window-all-closed', () => app.quit());
    app.on('before-quit', event => {
        if (quitting) return;
        quitting = true;
        event.preventDefault();
        if (window && !window.isDestroyed()) window.destroy();
        stopBackend().finally(() => app.quit());
    });
    app.whenReady().then(start).catch(error => {
        dialog.showErrorBox('AIRI Mini', error.message);
        app.quit();
    });
}
