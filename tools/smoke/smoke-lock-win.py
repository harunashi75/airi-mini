import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
from urllib.request import Request, urlopen

root = Path.cwd()
assets = root if len(sys.argv)>1 else Path(os.environ.get('AIRI_SMOKE_ASSETS', root / 'dist' / 'win-unpacked' / 'resources'))
model = next((root / 'models').glob('*.gguf'))

def win(path):
    return 'Z:' + str(path).replace('/', '\\')

with tempfile.TemporaryDirectory(prefix='airi-win-', dir=root / '.desktop-build') as folder:
    home = Path(folder)
    shutil.copytree(assets / 'config', home / 'config')
    shutil.copytree(assets / 'data.example', home / 'data')
    config_file = home / 'config' / 'airi.json'
    config = json.loads(config_file.read_text())
    config.update(model_path=win(model), max_tokens=8)
    config_file.write_text(json.dumps(config))
    env = os.environ | {'AIRIMINI_HOME': win(home), 'AIRIMINI_ASSETS': win(assets), 'AIRIMINI_DESKTOP_TOKEN': 'smoke-desktop', 'WINEDEBUG': '-all', 'PYTHONIOENCODING': 'utf-8'}
    with (home / 'backend.log').open('wb') as log:
        command = ['wine', assets / 'backend' / 'airi-backend.exe'] if len(sys.argv) == 1 else ['wine', root / '.desktop-build/windows-runtime/python.exe', '-u', '-c', "import sys; sys.path.insert(0, '.'); import desktop_backend; desktop_backend.main()"]
        p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env)
        try:
            line = p.stdout.readline().decode().strip()
            assert line.startswith('AIRIMINI_READY '), (line, (home / 'backend.log').read_text(errors='replace'))
            address = 'http://127.0.0.1:' + line.split()[1]
            auth = {'X-Airi-Desktop': 'smoke-desktop', 'Content-Type': 'application/json'}

            def api(route, payload=None):
                body = json.dumps(payload).encode() if payload is not None else None
                with urlopen(Request(address + route, headers=auth, data=body), timeout=45) as response:
                    return json.load(response)

            auth['X-Airi-Token'] = api('/api/bootstrap')['token']
            api('/api/memory', {'fact': 'Premier souvenir'})
            refused = subprocess.run(command, input=b'', capture_output=True, env=env, timeout=15)
            assert refused.returncode == 73, refused.stderr.decode(errors='replace')
            assert 'déjà utilisé' in refused.stderr.decode(errors='replace') or 'déjà utilisé' in refused.stderr.decode('cp1252', errors='replace')
            assert api('/api/memory')['facts'] == ['Premier souvenir']
            session = api('/api/sessions', {})['id']
            assert api('/api/chat', {'message': 'Bonjour !', 'session_id': session})['response']
            assert api('/api/sessions/' + session)['messages'][0]['content'] == 'Bonjour !'
            assert (home / 'data' / 'sessions' / (session + '.json.bak')).exists()
            p.stdin.close()
            assert p.wait(timeout=20) == 0, (home / 'backend.log').read_text(errors='replace')
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env) as next_process:
                try:
                    assert next_process.stdout.readline().decode().startswith('AIRIMINI_READY ')
                finally:
                    next_process.stdin.close()
                    assert next_process.wait(timeout=20) == 0
            print('Windows sous Wine : premier souvenir préservé, deuxième instance refusée, redémarrage OK')
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
