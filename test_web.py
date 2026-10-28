import csv
import http.client
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from acquisition import now_us
from calibration import SensorMonitor
from postprocess import analyze_recording
from test_acquisition import sample, wait_for
from web_server import WebApplication, make_server


def packet(seq=0):
    return dict(sample(seq=seq), fs_hz=30, raw=[28_000_000]*8, pressure=[100]*8,
                imu=[100, -200, 16384, 12, -7, 3], sensor_valid_mask=255, imu_valid=True)


class CalibrationTests(unittest.TestCase):
    def capture(self, monitor, kind):
        data = packet()
        monitor.begin(kind, {'left': dict(device_id='left', latest=data, age_s=0)}, duration=.1)
        start = monitor.capture['started']
        for index in range(6):
            result = monitor.observe(packet(index), start + index*33_333, None, 'in_order')
        return result

    def test_pressure_and_gyro_calibration_preserve_raw_and_gravity(self):
        monitor = SensorMonitor()
        result = self.capture(monitor, 'pressure')
        self.assertEqual(result['calibrated_pressure'], [0]*8)
        self.assertEqual(monitor.profiles['pressure']['left']['samples'], 5)
        result = self.capture(monitor, 'imu')
        self.assertEqual(result['corrected_imu'], [100, -200, 16384, 0, 0, 0])
        original = packet(7)
        before = json.dumps(original)
        monitor.observe(original, now_us(), None, 'in_order')
        self.assertEqual(json.dumps(original), before)
        invalid = monitor.observe(dict(packet(8), raw=[0]*8, sensor_valid_mask=0), now_us(), None, 'in_order')
        self.assertEqual(invalid['calibrated_pressure'], [None]*8)
        self.assertEqual(invalid['filtered_pressure'], [None]*8)

    def test_failed_recalibration_preserves_profile_but_reboot_invalidates_it(self):
        monitor = SensorMonitor(); self.capture(monitor, 'pressure')
        old = dict(monitor.profiles['pressure']['left'])
        monitor.begin('pressure', {'left': dict(device_id='left', latest=packet(), age_s=0)}, duration=.1)
        monitor.observe(dict(packet(), sensor_valid_mask=0), now_us(), None, 'in_order')
        self.assertIsNone(monitor.capture)
        self.assertEqual(monitor.profiles['pressure']['left'], old)
        monitor.observe(dict(packet(), boot_id='new-boot'), now_us(), None, 'restart')
        self.assertNotIn('left', monitor.profiles['pressure'])

    def test_moving_imu_and_missing_raw_are_rejected(self):
        monitor = SensorMonitor()
        with self.assertRaises(ValueError):
            monitor.begin('pressure', {'left': dict(device_id='left', latest=dict(packet(), raw=None), age_s=0)})
        monitor.begin('imu', {'left': dict(device_id='left', latest=packet(), age_s=0)}, duration=.1)
        start = monitor.capture['started']
        for index in range(6):
            monitor.observe(dict(packet(index), imu=[index*1000, 0, 16384, index*100, 0, 0]), start+index*33_333, None, 'in_order')
        self.assertNotIn('left', monitor.profiles['imu'])
        self.assertIn('IMU', monitor.message)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = WebApplication(self.temp.name, demo=True)
        self.server = make_server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        wait_for(lambda: len(self.app.receiver.snapshot()['devices']) == 2)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.app.close(); self.temp.cleanup()

    def request(self, method, path, data=None, extra_headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json'}
        headers.update(extra_headers or {})
        connection.request(method, path, json.dumps(data or {}) if method == 'POST' else None, headers)
        response = connection.getresponse(); status = response.status; content = response.read(); connection.close()
        return status, content

    def test_local_api_security_and_calibration_lock(self):
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/api/state')[0], 200)
        self.assertEqual(self.request('POST', '/api/calibrate', {'kind': 'pressure'}, {'Origin':'https://example.com'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', extra_headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/%2e%2e/acquisition.py')[0], 404)
        self.app.command('record/start', dict(subject='TEST', label='walking', notes=''))
        self.assertEqual(self.request('POST', '/api/calibrate', {'kind':'imu'})[0], 400)
        self.assertEqual(self.request('POST', '/api/disconnect', {})[0], 400)

    def test_record_stop_real_model_and_export(self):
        self.app.command('record/start', dict(subject='TEST', label='walking', notes=''))
        self.app.command('demo/motion', dict(motion='walking'))
        wait_for(lambda: self.app.receiver.recording.counts['left'] >= 160, timeout=10)
        self.app.command('record/stop', {})
        wait_for(lambda: self.app.analysis['status'] in ('done', 'error'), timeout=10)
        self.assertEqual(self.app.analysis['status'], 'done', self.app.analysis)
        result = self.app.analysis['result']
        self.assertGreater(result['windows'], 0, result)
        self.assertIn(result['result'], ('NORMAL', 'ABNORMAL'))
        status, payload = self.request('GET', '/api/result')
        self.assertEqual(status, 200)
        self.assertGreater(len(json.loads(payload)['timeline']), 0)
        self.assertNotIn('timeline', self.app.state()['analysis']['result'])
        path = self.app.session_path(result['session'])
        with (path / 'left.csv').open(newline='') as file:
            rows = list(csv.DictReader(file))
        self.assertIn('cal1', rows[0]); self.assertIn('filtered1', rows[0]); self.assertIn('sensor_valid_mask', rows[0])
        self.assertEqual(rows[0]['cal1'], '')
        meta = json.loads((path / 'metadata.json').read_text())
        self.assertEqual(meta['status'], 'complete')
        self.assertIn('calibration', meta)
        status, data = self.request('GET', '/api/export/' + result['session'])
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertTrue(any(name.endswith('result.json') for name in archive.namelist()))
            self.assertTrue(any(name.endswith('left.csv') for name in archive.namelist()))

    def test_standing_is_not_classified_as_normal(self):
        self.app.command('record/start', dict(subject='TEST', label='standing', notes=''))
        wait_for(lambda: self.app.receiver.recording.counts['left'] > 35)
        self.app.command('record/stop', {})
        wait_for(lambda: self.app.analysis['status'] in ('done', 'error'))
        result = self.app.analysis['result']
        self.assertEqual(result['status'], 'insufficient_data')
        self.assertIsNone(result['result'])
        self.assertEqual(result['windows'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
