from flask import Flask, request, jsonify, render_template, session, redirect
from flask_socketio import SocketIO, emit
import subprocess, psutil, os, json, threading, zipfile, shutil, signal
from werkzeug.utils import secure_filename
from pathlib import Path

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vps-super-secret-2024')
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

USERNAME = os.environ.get('VPS_USERNAME', 'admin')
PASSWORD = os.environ.get('VPS_PASSWORD', 'admin123')
PROJECTS_DIR = Path('projects')
PROJECTS_DIR.mkdir(exist_ok=True)

process_map = {}
logs_map = {}

# ─── helpers ───────────────────────────────────────────────────────────────────

def get_stats():
    return {
        'cpu':  round(psutil.cpu_percent(interval=0.1), 1),
        'ram':  round(psutil.virtual_memory().percent, 1),
        'disk': round(psutil.disk_usage('/').percent, 1),
    }

def load_config(proj_id):
    cfg_path = PROJECTS_DIR / proj_id / 'config.json'
    if not cfg_path.exists():
        return None
    with open(cfg_path) as f:
        return json.load(f)

def save_config(proj_id, cfg):
    with open(PROJECTS_DIR / proj_id / 'config.json', 'w') as f:
        json.dump(cfg, f)

def is_running(pid):
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False

def all_projects():
    result = []
    for d in PROJECTS_DIR.iterdir():
        if d.is_dir():
            cfg = load_config(d.name)
            if cfg:
                cfg['running'] = is_running(cfg.get('pid'))
                if not cfg['running']:
                    cfg['pid'] = None
                result.append(cfg)
    return result

def require_login():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return None

# ─── auth ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user' not in session:
        return redirect('/login')
    return render_template('index.html', username=session['user'])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        d = request.json or {}
        if d.get('username') == USERNAME and d.get('password') == PASSWORD:
            session['user'] = d['username']
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Wrong credentials'})
    return render_template('login.html')

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

# ─── stats ─────────────────────────────────────────────────────────────────────

@app.route('/api/stats')
def stats():
    err = require_login()
    if err: return err
    projects = all_projects()
    running_count = sum(1 for p in projects if p['running'])
    return jsonify({**get_stats(), 'running': running_count, 'total': len(projects)})

# ─── projects ──────────────────────────────────────────────────────────────────

@app.route('/api/projects', methods=['GET'])
def list_projects():
    err = require_login()
    if err: return err
    return jsonify(all_projects())

@app.route('/api/projects', methods=['POST'])
def create_project():
    err = require_login()
    if err: return err
    d = request.json or {}
    name = d.get('name', '').strip()
    runtime = d.get('runtime', 'python')
    if not name:
        return jsonify({'error': 'Name required'}), 400
    proj_id = name.lower().replace(' ', '-')
    proj_dir = PROJECTS_DIR / proj_id
    if proj_dir.exists():
        return jsonify({'error': 'Already exists'}), 400
    proj_dir.mkdir()
    cfg = {
        'id': proj_id, 'name': name, 'runtime': runtime,
        'main_file': 'main.py' if runtime == 'python' else 'index.js',
        'port': 8080, 'auto_restart': False, 'pid': None, 'running': False
    }
    save_config(proj_id, cfg)
    return jsonify({'success': True, 'project': cfg})

@app.route('/api/projects/<proj_id>', methods=['DELETE'])
def delete_project(proj_id):
    err = require_login()
    if err: return err
    proj_dir = PROJECTS_DIR / proj_id
    if not proj_dir.exists():
        return jsonify({'error': 'Not found'}), 404
    cfg = load_config(proj_id)
    if cfg and cfg.get('pid'):
        try: os.kill(cfg['pid'], signal.SIGTERM)
        except Exception: pass
    shutil.rmtree(proj_dir)
    return jsonify({'success': True})

# ─── start / stop ──────────────────────────────────────────────────────────────

@app.route('/api/projects/<proj_id>/start', methods=['POST'])
def start_project(proj_id):
    err = require_login()
    if err: return err
    cfg = load_config(proj_id)
    if not cfg:
        return jsonify({'error': 'Not found'}), 404

    runtime = cfg.get('runtime', 'python')
    main_file = cfg.get('main_file', 'main.py')
    cmd = ['python', '-u', main_file] if runtime == 'python' else ['node', main_file]
    logs_map[proj_id] = []

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECTS_DIR / proj_id),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        process_map[proj_id] = proc
        cfg['pid'] = proc.pid
        save_config(proj_id, cfg)

        def stream_logs():
            for line in iter(proc.stdout.readline, ''):
                clean = line.rstrip()
                logs_map.setdefault(proj_id, []).append(clean)
                socketio.emit('log', {'project': proj_id, 'line': clean})
            proc.stdout.close()
            proc.wait()
            c = load_config(proj_id)
            if c:
                c['pid'] = None
                save_config(proj_id, c)
            socketio.emit('process_stopped', {'project': proj_id})

        threading.Thread(target=stream_logs, daemon=True).start()
        return jsonify({'success': True, 'pid': proc.pid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/projects/<proj_id>/stop', methods=['POST'])
def stop_project(proj_id):
    err = require_login()
    if err: return err
    cfg = load_config(proj_id)
    if not cfg:
        return jsonify({'error': 'Not found'}), 404
    pid = cfg.get('pid')
    if pid:
        try: os.kill(pid, signal.SIGTERM)
        except Exception: pass
        cfg['pid'] = None
        save_config(proj_id, cfg)
    return jsonify({'success': True})

# ─── logs ──────────────────────────────────────────────────────────────────────

@app.route('/api/projects/<proj_id>/logs')
def get_logs(proj_id):
    err = require_login()
    if err: return err
    return jsonify({'logs': logs_map.get(proj_id, [])})

@app.route('/api/projects/<proj_id>/logs/clear', methods=['POST'])
def clear_logs(proj_id):
    err = require_login()
    if err: return err
    logs_map[proj_id] = []
    return jsonify({'success': True})

# ─── files ─────────────────────────────────────────────────────────────────────

@app.route('/api/projects/<proj_id>/files', methods=['GET'])
def list_files(proj_id):
    err = require_login()
    if err: return err
    proj_dir = PROJECTS_DIR / proj_id
    files = []
    for f in proj_dir.iterdir():
        if f.name != 'config.json' and f.is_file():
            files.append({'name': f.name, 'size': f.stat().st_size})
    return jsonify({'files': files})

@app.route('/api/projects/<proj_id>/files', methods=['POST'])
def upload_or_create_file(proj_id):
    err = require_login()
    if err: return err
    proj_dir = PROJECTS_DIR / proj_id

    if 'file' in request.files:
        file = request.files['file']
        fname = secure_filename(file.filename)
        dest = proj_dir / fname
        file.save(str(dest))
        if fname.endswith('.zip'):
            with zipfile.ZipFile(str(dest), 'r') as z:
                z.extractall(str(proj_dir))
            os.remove(str(dest))
        return jsonify({'success': True})

    d = request.json or {}
    fname = secure_filename(d.get('name', 'untitled.py'))
    (proj_dir / fname).write_text(d.get('content', ''))
    return jsonify({'success': True})

@app.route('/api/projects/<proj_id>/files/<filename>', methods=['GET'])
def read_file(proj_id, filename):
    err = require_login()
    if err: return err
    fp = PROJECTS_DIR / proj_id / secure_filename(filename)
    if not fp.exists():
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'content': fp.read_text(errors='replace')})

@app.route('/api/projects/<proj_id>/files/<filename>', methods=['PUT'])
def write_file(proj_id, filename):
    err = require_login()
    if err: return err
    fp = PROJECTS_DIR / proj_id / secure_filename(filename)
    fp.write_text((request.json or {}).get('content', ''))
    return jsonify({'success': True})

@app.route('/api/projects/<proj_id>/files/<filename>', methods=['DELETE'])
def remove_file(proj_id, filename):
    err = require_login()
    if err: return err
    fp = PROJECTS_DIR / proj_id / secure_filename(filename)
    if fp.exists():
        fp.unlink()
    return jsonify({'success': True})

# ─── packages ──────────────────────────────────────────────────────────────────

@app.route('/api/projects/<proj_id>/packages', methods=['GET'])
def list_packages(proj_id):
    err = require_login()
    if err: return err
    result = subprocess.run(['pip', 'list', '--format=json'], capture_output=True, text=True)
    try:
        pkgs = json.loads(result.stdout)
    except Exception:
        pkgs = []
    return jsonify({'packages': pkgs})

@app.route('/api/projects/<proj_id>/packages', methods=['POST'])
def install_package(proj_id):
    err = require_login()
    if err: return err
    d = request.json or {}
    pkg = d.get('package', '').strip()
    ver = d.get('version', '').strip()
    pkg_str = f"{pkg}=={ver}" if ver else pkg
    if not pkg:
        return jsonify({'error': 'Package name required'}), 400

    def do_install():
        socketio.emit('package_log', {'project': proj_id, 'line': f'Installing {pkg_str}...'})
        result = subprocess.run(
            ['pip', 'install', pkg_str],
            capture_output=True, text=True
        )
        for line in (result.stdout + result.stderr).splitlines():
            socketio.emit('package_log', {'project': proj_id, 'line': line})
        socketio.emit('package_done', {
            'project': proj_id,
            'success': result.returncode == 0,
            'package': pkg_str
        })

    threading.Thread(target=do_install, daemon=True).start()
    return jsonify({'success': True})

# ─── console ───────────────────────────────────────────────────────────────────

@app.route('/api/projects/<proj_id>/console', methods=['POST'])
def run_command(proj_id):
    err = require_login()
    if err: return err
    cmd = (request.json or {}).get('command', '').strip()
    if not cmd:
        return jsonify({'output': '', 'returncode': 0})
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=str(PROJECTS_DIR / proj_id),
            capture_output=True, text=True, timeout=30
        )
        return jsonify({'output': result.stdout + result.stderr, 'returncode': result.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({'output': 'Timeout (30s limit)', 'returncode': -1})
    except Exception as e:
        return jsonify({'output': str(e), 'returncode': -1})

# ─── settings ──────────────────────────────────────────────────────────────────

@app.route('/api/projects/<proj_id>/settings', methods=['PUT'])
def update_settings(proj_id):
    err = require_login()
    if err: return err
    cfg = load_config(proj_id)
    if not cfg:
        return jsonify({'error': 'Not found'}), 404
    d = request.json or {}
    cfg['main_file']    = d.get('main_file',    cfg['main_file'])
    cfg['port']         = d.get('port',         cfg['port'])
    cfg['auto_restart'] = d.get('auto_restart', cfg['auto_restart'])
    save_config(proj_id, cfg)
    return jsonify({'success': True})

# ─── run ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
