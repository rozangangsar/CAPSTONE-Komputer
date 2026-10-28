"""UDP acquisition and append-only research recordings; Python standard library."""
from __future__ import annotations

import csv
import json
import math
import socket
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from sensor_contract import SENSOR_LAYOUT, SAMPLING_RATE


def now_us():
    # perf_counter uses Windows' high-resolution monotonic clock, including Python 3.11/3.12.
    return time.perf_counter_ns() // 1000


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name}: expected integer {minimum}..{maximum}")
    return value


def identifier(value, name):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError(f"{name}: expected 1..64 characters")
    if not all(c.isascii() and (c.isalnum() or c in '-_.') for c in value):
        raise ValueError(f"{name}: use ASCII letters, numbers, -, _ or .")
    return value


def numbers(value, name, count):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{name}: expected {count} numbers")
    for item in value:
        if type(item) not in (int, float) or abs(item) > 1e12 or not math.isfinite(item):
            raise ValueError(f"{name}: invalid numeric value")
    return value


def parse_packet(raw, sensor_count=8):
    if len(raw) > 4096:
        raise ValueError('Datagram exceeds 4096 bytes')
    def reject_constant(value):
        raise ValueError(f'Non-finite JSON constant: {value}')
    data = json.loads(raw.decode('utf-8'), parse_constant=reject_constant)
    if not isinstance(data, dict) or type(data.get('v')) is not int or data['v'] != 1:
        raise ValueError('Expected protocol v=1')
    if data.get('type') not in ('sample', 'sync_reply'):
        raise ValueError('Unknown packet type')
    if data.get('foot') not in ('left', 'right'):
        raise ValueError('foot must be left or right')
    for key in ('device_id', 'boot_id'):
        identifier(data.get(key), key)
    if data['type'] == 'sample':
        for key in ('seq', 'sample_index', 't_us'):
            integer(data.get(key), key)
        integer(data.get('fs_hz'), 'fs_hz', 1, 2000)
        if 'sensor_valid_mask' in data:
            integer(data['sensor_valid_mask'], 'sensor_valid_mask', 0, 255)
        if 'imu_valid' in data and type(data['imu_valid']) is not bool:
            raise ValueError('imu_valid must be boolean')
        numbers(data.get('pressure'), 'pressure', sensor_count)
        if data.get('raw') is not None:
            numbers(data['raw'], 'raw', sensor_count)
        if data.get('imu') is not None:
            numbers(data['imu'], 'imu', 6)
            # Current FDC firmware reads BMI160 gyro registers before accel.
            if sensor_count == 8:
                data['imu_wire_gyro_first'] = list(data['imu'])
                data['imu'] = data['imu'][3:] + data['imu'][:3]
    else:
        identifier(data.get('nonce'), 'nonce')
        integer(data.get('e2_us'), 'e2_us')
        integer(data.get('e3_us'), 'e3_us', data['e2_us'])
    return data


class Recording:
    def __init__(self, root, subject, label, notes, devices, sensor_count=8, cnn=None, extra_metadata=None):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.path = Path(root) / f'{stamp}_{uuid.uuid4().hex[:8]}'
        self.path.mkdir(parents=True, exist_ok=False)
        self.files = []
        self.counts = {'left': 0, 'right': 0}
        self.metadata = dict(schema_version=1, session_id=self.path.name,
                             subject=subject, label=label, notes=notes,
                             started_utc=utc_now(), started_host_us=now_us(),
                             status='recording', devices=devices,
                             timestamp_basis='host monotonic microseconds',
                             fsr_per_foot=sensor_count,
                             sensor_profile='TRIAL4' if sensor_count == 4 else 'PRODUCTION8',
                             cnn=cnn, counter_scope='receiver lifetime, not session-only',
                             alignment='raw streams; offset estimates only; no resampling')
        self.metadata.update(source_sampling_rate_hz=SAMPLING_RATE, sensor_layout=SENSOR_LAYOUT,
                             declared_rates_by_foot={foot: state['latest']['fs_hz'] for foot, state in devices.items()},
                             imu_order=['ax','ay','az','gx','gy','gz'],
                             sensor_units='ESP baseline-adjusted counts, not calibrated pressure',
                             raw_units='FDC 28-bit counts when supplied',
                             pressure_transform='clamp((ESP_boot_unloaded_baseline - FDC_raw)/2000,0,65535)',
                             amplitude_flags_available=False,
                             recording_sides=[foot for foot, state in devices.items() if state['age_s'] <= 3])
        self.metadata_path = self.path / 'metadata.json'
        self.metadata.update(extra_metadata or {})
        try:
            self.metadata_path.write_text(json.dumps(self.metadata, indent=2), encoding='utf-8')
            raw = (self.path / 'packets.jsonl').open('x', encoding='utf-8')
            self.files.append(raw)
            self.raw = raw
            self.writers = {}
            columns = ['host_rx_us', 'host_rx_utc', 'source_ip', 'source_port',
                       'device_id', 'boot_id', 'seq', 'sample_index', 'esp_t_us',
                       'fs_hz', 'sequence_status', 'gap_observed', 'clock_offset_us',
                       'clock_rtt_us', 'clock_age_us', 'estimated_host_sample_us']
            columns += [f'p{i}' for i in range(1, 9)]
            columns += ['ax', 'ay', 'az', 'gx', 'gy', 'gz']
            columns += [f'raw{i}' for i in range(1, 9)]
            columns += ['sensor_valid_mask', 'imu_valid']
            columns += [f'cal{i}' for i in range(1, 9)] + [f'filtered{i}' for i in range(1, 9)]
            columns += [f'corrected_{axis}' for axis in ('ax', 'ay', 'az', 'gx', 'gy', 'gz')]
            for foot in ('left', 'right'):
                file = (self.path / f'{foot}.csv').open('x', newline='', encoding='utf-8')
                self.files.append(file)
                writer = csv.writer(file)
                writer.writerow(columns)
                self.writers[foot] = writer
            predictions = (self.path / 'predictions.csv').open('x', newline='', encoding='utf-8')
            self.files.append(predictions)
            self.prediction_columns = ['window_start_us', 'window_end_us', 'label', 'confidence',
                                       'abnormal_probability', 'window_probability', 'inference_ms',
                                       'averaged_windows', 'model_version']
            self.predictions = csv.DictWriter(predictions, fieldnames=self.prediction_columns, extrasaction='ignore')
            self.predictions.writeheader()
            self.prediction_count = 0
            self.last_flush = time.monotonic()
            self.flush()
        except Exception:
            for file in self.files:
                file.close()
            raise

    def append(self, data, address, rx, sequence_status, gap, clock, processed=None):
        rx_utc = utc_now()
        record = dict(host_rx_us=rx, host_rx_utc=rx_utc,
                      source=list(address), packet=data)
        self.raw.write(json.dumps(record, allow_nan=False) + '\n')
        if data['type'] == 'sample':
            offset = clock['offset_us'] if clock else ''
            row = [rx, rx_utc, *address, data['device_id'], data['boot_id'],
                   data['seq'], data['sample_index'], data['t_us'], data['fs_hz'],
                   sequence_status, gap, offset, clock['rtt_us'] if clock else '',
                   rx - clock['measured_host_us'] if clock else '',
                   data['t_us'] + offset if clock else '']
            row += data['pressure'] + [''] * (8 - len(data['pressure'])) + (data.get('imu') or [''] * 6)
            row += (data.get('raw') or [''] * 8) + ([''] * (8 - len(data['raw'])) if data.get('raw') else [])
            row += [data.get('sensor_valid_mask', 255), data.get('imu_valid', data.get('imu') is not None)]
            processed = processed or {}
            row += processed.get('calibrated_pressure') or [''] * 8
            row += processed.get('filtered_pressure') or [''] * 8
            row += processed.get('corrected_imu') or [''] * 6
            self.writers[data['foot']].writerow(row)
            self.counts[data['foot']] += 1
        if time.monotonic() - self.last_flush >= 1:
            self.flush()

    def flush(self):
        for file in self.files:
            file.flush()
        self.last_flush = time.monotonic()

    def append_prediction(self, result):
        self.predictions.writerow(result)
        self.prediction_count += 1

    def finish(self, status, stats):
        errors = []
        for file in self.files:
            try:
                file.close()
            except OSError as error:
                errors.append(str(error))
        self.metadata.update(ended_utc=utc_now(), ended_host_us=now_us(),
                             status='error' if errors else status,
                             sample_rows=self.counts, counters=stats,
                             prediction_rows=self.prediction_count,
                             close_errors=errors)
        temporary = self.metadata_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.metadata, indent=2), encoding='utf-8')
        temporary.replace(self.metadata_path)
        if errors:
            raise OSError('; '.join(errors))


class Receiver:
    def __init__(self, sensor_count=8, analysis=None, observer=None):
        if sensor_count not in (4, 8):
            raise ValueError('Sensor profile must be 4 (trial) or 8 (production)')
        self.sensor_count = sensor_count
        self.analysis = analysis
        self.observer = observer
        self.lock = threading.RLock()
        self.sock = None
        self.thread = None
        self.stop_event = threading.Event()
        self.recording = None
        self.devices = {}
        self.pending = {}
        self.stats = dict(valid=0, invalid=0, rejected=0, gaps=0, duplicates=0,
                          out_of_order=0, restarts=0)
        self.error = ''
        self.last_path = ''

    def start(self, host, port):
        with self.lock:
            if self.sock is not None:
                raise ValueError('Receiver already running')
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                sock.bind((host, integer(port, 'port', 0, 65535)))
                sock.settimeout(0.15)
            except Exception:
                sock.close()
                raise
            self.sock = sock
            self.devices.clear()
            self.pending.clear()
            self.stats = dict.fromkeys(self.stats, 0)
            self.error = ''
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return sock.getsockname()[1]

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    raw, address = self.sock.recvfrom(4097)
                    rx = now_us()
                    try:
                        data = parse_packet(raw, self.sensor_count)
                    except (ValueError, UnicodeError, RecursionError, OverflowError):
                        with self.lock:
                            self.stats['invalid'] += 1
                    else:
                        with self.lock:
                            self._handle(data, address, rx)
                except socket.timeout:
                    pass
                with self.lock:
                    self._sync_due()
                    self._save_predictions()
                    if self.recording and time.monotonic() - self.recording.last_flush >= 1:
                        self.recording.flush()
        except Exception as error:
            with self.lock:
                self.error = f'Acquisition stopped: {error}'
                self.stop_event.set()
                try:
                    self.stop_recording('error')
                except OSError as close_error:
                    self.error += f'; close failed: {close_error}'

    def _handle(self, data, address, rx):
        foot = data['foot']
        dev = self.devices.get(foot)
        if dev and (dev['device_id'] != data['device_id'] or dev['address'] != address):
            self.stats['rejected'] += 1
            return
        if data['type'] == 'sync_reply':
            self._sync_reply(data, address, rx, dev)
            return
        if not dev:
            dev = dict(device_id=data['device_id'], address=address,
                       boot_id=data['boot_id'], high=-1, seen=set(), recent=deque(),
                       rows=0, gaps=0, syncs=deque(maxlen=20), clock=None, next_sync=0,
                       arrivals=deque(maxlen=4000))
            self.devices[foot] = dev
        status, gap = 'in_order', 0
        if dev['boot_id'] != data['boot_id']:
            self.stats['restarts'] += 1
            dev.update(boot_id=data['boot_id'], high=-1, clock=None, next_sync=0)
            dev['syncs'].clear()
            dev['seen'].clear()
            dev['recent'].clear()
            status = 'restart'
        seq = data['seq']
        if seq in dev['seen']:
            status = 'duplicate'
            self.stats['duplicates'] += 1
        else:
            if seq < dev['high']:
                status = 'out_of_order'
                self.stats['out_of_order'] += 1
            elif dev['high'] >= 0 and seq > dev['high'] + 1:
                gap = seq - dev['high'] - 1
                status = 'gap'
                self.stats['gaps'] += gap
                dev['gaps'] += gap
            dev['high'] = max(seq, dev['high'])
            dev['seen'].add(seq)
            dev['recent'].append(seq)
            if len(dev['recent']) > 4096:
                dev['seen'].discard(dev['recent'].popleft())
        self.stats['valid'] += 1
        dev['rows'] += 1
        dev['arrivals'].append(rx)
        dev.update(last_rx=rx, latest=data)
        if dev['clock'] and rx - dev['clock']['measured_host_us'] > 30_000_000:
            dev['clock'] = None
        processed = self.observer(data, rx, dev['clock'], status) if self.observer else None
        if self.recording:
            self.recording.append(data, address, rx, status, gap, dev['clock'], processed)
        if self.analysis:
            self.analysis.submit(data, rx, dev['clock'], status)

    def _save_predictions(self):
        if self.analysis:
            for result in self.analysis.drain_results():
                if (self.recording and
                        result['window_start_us'] >= self.recording.metadata['started_host_us']):
                    self.recording.metadata['cnn'] = self.analysis.snapshot()['model']
                    self.recording.append_prediction(result)

    def _sync_due(self):
        current = now_us()
        self.pending = {key: value for key, value in self.pending.items()
                        if current - value['h1'] < 2_000_000}
        for foot, dev in self.devices.items():
            if current - dev['last_rx'] > 3_000_000 or current < dev['next_sync']:
                continue
            nonce = uuid.uuid4().hex[:16]
            h1 = now_us()
            self.pending[nonce] = dict(h1=h1, foot=foot, address=dev['address'],
                                       boot_id=dev['boot_id'])
            packet = dict(v=1, type='sync', nonce=nonce, h1_us=h1)
            self.sock.sendto(json.dumps(packet).encode(), dev['address'])
            dev['next_sync'] = current + (250_000 if len(dev['syncs']) < 8 else 5_000_000)

    def _sync_reply(self, data, address, h4, dev):
        pending = self.pending.get(data['nonce'])
        if (not pending or not dev or pending['address'] != address or
                pending['foot'] != data['foot'] or pending['boot_id'] != data['boot_id']):
            self.stats['rejected'] += 1
            return
        del self.pending[data['nonce']]
        h1, e2, e3 = pending['h1'], data['e2_us'], data['e3_us']
        rtt = (h4 - h1) - (e3 - e2)
        if not 0 <= rtt <= 500_000:
            self.stats['rejected'] += 1
            return
        measurement = dict(offset_us=((h1 - e2) + (h4 - e3)) / 2,
                           rtt_us=rtt, measured_host_us=h4)
        dev['syncs'].append(measurement)
        recent = [item for item in dev['syncs'] if h4 - item['measured_host_us'] < 20_000_000]
        dev['clock'] = min(recent, key=lambda item: item['rtt_us'])
        if self.recording:
            # Save H1 as well, so offline processing can fit offset AND drift.
            self.recording.append(dict(data, h1_us=h1), address, h4, '', 0, None)

    def snapshot(self):
        with self.lock:
            current = now_us()
            devices = {}
            for foot, dev in self.devices.items():
                rate = sum(t >= current - 1_000_000 for t in dev['arrivals'])
                clock = dev['clock']
                if clock and current - clock['measured_host_us'] > 30_000_000:
                    clock = None
                devices[foot] = dict(device_id=dev['device_id'], address=dev['address'],
                                      age_s=(current - dev['last_rx']) / 1e6,
                                      rows=dev['rows'], gaps=dev['gaps'], rate=rate,
                                      latest=dev['latest'], clock=clock)
            return dict(running=self.sock is not None and not self.stop_event.is_set(),
                        recording=self.recording is not None, devices=devices,
                        stats=dict(self.stats), error=self.error, path=self.last_path,
                        counts=dict(self.recording.counts) if self.recording else {})

    def start_recording(self, root, subject, label, notes, extra_metadata=None):
        with self.lock:
            if self.recording:
                raise ValueError('Recording already active')
            snap = self.snapshot()
            live = [state for state in snap['devices'].values() if state['age_s'] <= 3]
            if not snap['running'] or not live:
                raise ValueError('At least one foot must be streaming before recording')
            if len({state['latest']['fs_hz'] for state in live}) > 1:
                raise ValueError('Both feet must declare the same sample rate')
            if not subject.strip() or not label.strip():
                raise ValueError('Subject ID and label are required')
            self.recording = Recording(root, subject.strip(), label.strip(), notes.strip(), snap['devices'],
                                       self.sensor_count, self.analysis.snapshot()['model'] if self.analysis else None,
                                       extra_metadata)
            self.last_path = str(self.recording.path)
            return self.last_path

    def stop_recording(self, status='complete'):
        with self.lock:
            if self.recording:
                self._save_predictions()
                recording, self.recording = self.recording, None
                recording.finish(status, dict(self.stats))

    def stop(self):
        if self.analysis:
            self.analysis.stop()
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError('Receiver is still finishing disk I/O; retry Stop')
        with self.lock:
            try:
                self.stop_recording()
            finally:
                if self.sock:
                    self.sock.close()
                    self.sock = None
