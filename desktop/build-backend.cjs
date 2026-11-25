'use strict';

const { spawnSync } = require('node:child_process');
const { existsSync, rmSync } = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const platform = process.platform === 'win32' ? 'win' : 'linux';
const windows = platform === 'win';
const localPython = path.join(root, 'venv', windows ? 'Scripts' : 'bin', windows ? 'python.exe' : 'python');
const python = process.env.AIRIMINI_PYTHON || (existsSync(localPython) ? localPython : windows ? 'python' : 'python3');
const work = path.join(root, '.desktop-build');

if (!['win32', 'linux'].includes(process.platform) || process.arch !== 'x64') {
    console.error('Utilise Windows ou Linux x64 pour construire le moteur natif.');
    process.exit(1);
}

const result = spawnSync(python, [
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--noupx', '--name', 'airi-backend',
    '--distpath', path.join(work, platform), '--workpath', path.join(work, 'work', platform),
    '--specpath', path.join(work, platform), '--paths', root, '--collect-binaries', 'llama_cpp',
    '--recursive-copy-metadata', 'fastapi', '--recursive-copy-metadata', 'uvicorn',
    '--recursive-copy-metadata', 'llama-cpp-python', '--copy-metadata', 'setuptools',
    '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.asyncio',
    '--hidden-import', 'uvicorn.protocols.http.h11_impl', '--hidden-import', 'uvicorn.lifespan.on',
    path.join(root, 'desktop_backend.py')
], { cwd: root, stdio: 'inherit' });

if (result.error) console.error(result.error.message);
if (result.status !== 0) process.exit(result.status ?? 1);

if (!windows) {
    // Electron already requires these system libraries. Copies from the build host
    // can raise the packaged backend's minimum glibc version.
    const internal = path.join(work, platform, 'airi-backend', '_internal');
    for (const library of ['libstdc++.so.6', 'libgcc_s.so.1']) {
        rmSync(path.join(internal, library), { force: true });
    }
}
