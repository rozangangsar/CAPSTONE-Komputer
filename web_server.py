"""Local-only web interface for the existing UDP receiver. No frontend build required."""
import argparse
import json
import mimetypes
import re
import tempfile
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from acquisition import Receiver, now_us
from calibration import SensorMonitor, FILTER_TAU_SECONDS
from sensor_contract import SAMPLING_RATE, SENSOR_LAYOUT

BASE = Path(__file__).resolve().parent


class WebApplication:
    def __init__(self, recordings=None, demo=False):
        self.lock = threading.RLock()
        self.monitor = SensorMonitor()
        self.receiver = Receiver(observer=self.monitor.observe)
        self.recordings = Path(recordings or BASE / 'recordings').resolve()
        self.demo = demo
        self.simulator = None
        self.analysis = dict(status='idle', result=None, error='')
        self.analysis_thread = None
        self.record_started = None
        if demo:
            from simulator import Simulator
            port = self.receiver.start('127.0.0.1', 0)
            self.simulator = Simulator('127.0.0.1', port, hz=SAMPLING_RATE)
            self.simulator.motion = 'standing'
            self.simulator.start()

    def session_path(self, session):
        if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', session):
            raise ValueError('ID sesi tidak valid')
        path = (self.recordings / session).resolve()
        if path.parent != self.recordings or not (path / 'metadata.json').is_file():
            raise ValueError('Sesi tidak ditemukan')
        return path

    def sessions(self):
        entries = []
        for path in sorted(self.recordings.glob('*/metadata.json'), reverse=True)[:100]:
            try:
                meta = json.loads(path.read_text(encoding='utf-8'))
                entries.append(dict(id=path.parent.name, subject=meta['subject'], label=meta['label'],
                                    started=meta['started_utc'], status=meta['status'], counts=meta.get('sample_rows', {})))
            except (ValueError, KeyError, OSError):
                continue
        return entries

    def state(self):
        with self.lock:
            state = self.receiver.snapshot()
            state['path'] = Path(state['path']).name if state['path'] else None
            analysis = dict(self.analysis)
            if analysis['result']:
                analysis['result'] = {key: value for key, value in analysis['result'].items() if key != 'timeline'}
            state.update(calibration=self.monitor.snapshot(), analysis=analysis,
                         demo=self.demo, demo_motion=self.simulator.motion if self.simulator else None,
                         layout=SENSOR_LAYOUT, sampling_rate=SAMPLING_RATE,
                         elapsed=(now_us()-self.record_started)/1e6 if state['recording'] and self.record_started else 0)
            return state

    def start_analysis(self, path):
        if self.analysis['status'] == 'running':
            raise ValueError('Analisis sebelumnya masih berjalan')
        self.analysis = dict(status='running', result=None, error='')
        def work():
            from postprocess import analyze_recording
            try:
                result = analyze_recording(path)
                with self.lock:
                    self.analysis = dict(status='done', result=result, error='')
            except Exception as error:
                with self.lock:
                    self.analysis = dict(status='error', result=None, error=str(error))
        self.analysis_thread = threading.Thread(target=work, daemon=True)
        self.analysis_thread.start()

    def command(self, action, data):
        with self.lock:
            if action == 'connect':
                if self.demo:
                    raise ValueError('Demo sudah menerima data pada port loopback terpisah')
                port = data.get('port', 9000)
                if type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError('Port harus 1..65535')
                self.receiver.start('0.0.0.0', port)
            elif action == 'disconnect':
                if self.receiver.recording or self.monitor.snapshot()['active']:
                    raise ValueError('Selesaikan rekaman atau kalibrasi terlebih dahulu')
                self.receiver.stop()
                if self.simulator:
                    self.simulator.stop()
            elif action == 'calibrate':
                if self.receiver.recording:
                    raise ValueError('Kalibrasi dikunci selama rekaman')
                self.monitor.begin(data.get('kind'), self.receiver.snapshot()['devices'])
            elif action == 'record/start':
                if self.monitor.snapshot()['active']:
                    raise ValueError('Tunggu kalibrasi selesai')
                if self.analysis['status'] == 'running':
                    raise ValueError('Tunggu analisis selesai')
                subject, label, notes = (data.get(key, '').strip() for key in ('subject', 'label', 'notes'))
                if len(subject) > 64 or len(label) > 64 or len(notes) > 2000:
                    raise ValueError('ID/label maksimal 64 karakter, catatan maksimal 2000')
                if self.demo:
                    label = 'SIMULATED-' + label
                self.receiver.start_recording(self.recordings, subject, label, notes, extra_metadata=dict(
                    calibration=self.monitor.snapshot()['profiles'],
                    host_processing=dict(version=1, median_window=3, ema_tau_seconds=FILTER_TAU_SECONDS,
                                         calibrated_pressure='(session_baseline-raw)/2000, signed response counts',
                                         imu_correction='gyro bias only, acceleration including gravity preserved'),
                    acquisition_interface='localhost-web'))
                self.record_started = self.receiver.recording.metadata['started_host_us']
                self.analysis = dict(status='idle', result=None, error='')
            elif action == 'record/stop':
                if not self.receiver.recording:
                    raise ValueError('Tidak ada rekaman aktif')
                path = Path(self.receiver.last_path)
                self.receiver.stop_recording()
                self.record_started = None
                self.start_analysis(path)
            elif action == 'analyze':
                path = self.session_path(data.get('session'))
                if self.receiver.recording:
                    raise ValueError('Hentikan rekaman sebelum analisis')
                self.start_analysis(path)
            elif action == 'demo/motion' and self.simulator:
                if data.get('motion') not in ('standing', 'walking'):
                    raise ValueError('Gerakan demo tidak dikenal')
                if self.monitor.snapshot()['active']:
                    raise ValueError('Tunggu kalibrasi selesai')
                self.simulator.motion = data['motion']
            else:
                raise ValueError('Aksi tidak dikenal')
            return dict(ok=True)

    def close(self):
        if self.simulator:
            self.simulator.stop()
        self.receiver.stop()
        if self.analysis_thread:
            self.analysis_thread.join(timeout=10)


def make_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def reply(self, code, value):
            payload = json.dumps(value, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, dict(error='Host tidak diizinkan'))
            path = unquote(urlparse(self.path).path)
            try:
                if path == '/api/state':
                    return self.reply(200, app.state())
                if path == '/api/sessions':
                    return self.reply(200, app.sessions())
                if path == '/api/result':
                    with app.lock:
                        return self.reply(200, app.analysis['result'])
                if path.startswith('/api/export/'):
                    folder = app.session_path(path.removeprefix('/api/export/'))
                    if app.receiver.recording and folder == app.receiver.recording.path:
                        raise ValueError('Simpan rekaman sebelum ekspor')
                    if app.analysis['status'] == 'running':
                        raise ValueError('Tunggu analisis selesai sebelum ekspor')
                    with tempfile.SpooledTemporaryFile(max_size=8*1024*1024) as archive:
                        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                            for file in folder.rglob('*'):
                                if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(folder):
                                    zip_file.write(file, arcname=f'{folder.name}/{file.relative_to(folder)}')
                        length = archive.tell()
                        archive.seek(0)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Disposition', f'attachment; filename="{folder.name}.zip"')
                        self.send_header('Content-Length', str(length))
                        self.send_header('Cache-Control', 'no-store')
                        self.end_headers()
                        for chunk in iter(lambda: archive.read(65536), b''):
                            self.wfile.write(chunk)
                    return
                static = (BASE / 'web' / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
                if not static.is_relative_to(BASE / 'web') or not static.is_file():
                    return self.reply(404, dict(error='Tidak ditemukan'))
                content = static.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'font/woff2' if static.suffix == '.woff2' else mimetypes.guess_type(static)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(content)
            except (ValueError, OSError) as error:
                self.reply(400, dict(error=str(error)))

        def do_POST(self):
            origin = self.headers.get('Origin')
            allowed = (None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
            if not self.valid_host() or origin not in allowed or self.headers.get('Content-Type') != 'application/json':
                return self.reply(403, dict(error='Permintaan harus berasal dari aplikasi lokal'))
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Ukuran permintaan tidak valid')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Objek JSON dibutuhkan')
                result = app.command(urlparse(self.path).path.removeprefix('/api/'), data)
                self.reply(200, result)
            except (ValueError, OSError, RuntimeError, TypeError, AttributeError) as error:
                self.reply(400, dict(error=str(error)))

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    app = WebApplication(demo=args.demo)
    try:
        server = make_server(app, args.port)
        url = f'http://127.0.0.1:{server.server_port}'
        print(f'Smart Insole: {url}', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
