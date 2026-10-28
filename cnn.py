"""Live gait classification using the Android test model, off the UDP/UI threads."""
import hashlib
import json
import math
import queue
import threading
import time
from collections import deque
from pathlib import Path
from sensor_contract import SAMPLING_RATE, INPUT_LENGTH, STRIDE

MODEL_DIR = Path(__file__).resolve().parent / 'models'
CHANNELS = [f'{foot}_{name}' for foot in ('left', 'right')
            for name in ([f'fsr{i}' for i in range(1, 9)] +
                         [f'imu_{axis}' for axis in ('ax', 'ay', 'az', 'gx', 'gy', 'gz')])]


class GaitModel:
    def __init__(self):
        import numpy as np
        from ai_edge_litert.interpreter import Interpreter
        self.np = np
        self.metadata = json.loads((MODEL_DIR / 'gait_test_model.json').read_text(encoding='utf-8'))
        meta = self.metadata
        if (meta['channelNames'] != CHANNELS or meta['inputShape'] != [1, INPUT_LENGTH, 28] or
                meta['samplingRate'] != SAMPLING_RATE or meta['inputLength'] != INPUT_LENGTH or
                meta['stride'] != STRIDE or meta['classNames'] != ['NORMAL', 'ABNORMAL'] or
                not 0 < meta['decisionThreshold'] < 1):
            raise ValueError('Metadata CNN tidak sesuai kontrak 30 Hz / 60 x 28 / NORMAL-ABNORMAL')
        self.mean = np.asarray(meta['normalizationMean'], dtype=np.float32)
        self.std = np.asarray(meta['normalizationStd'], dtype=np.float32)
        if (self.mean.shape != (28,) or self.std.shape != (28,) or
                not np.isfinite(self.mean).all() or not np.isfinite(self.std).all() or
                not (self.std > 0).all()):
            raise ValueError('Normalisasi CNN tidak valid')
        model_bytes = (MODEL_DIR / 'gait_test_model.tflite').read_bytes()
        self.sha256 = hashlib.sha256(model_bytes).hexdigest()
        self.interpreter = Interpreter(model_content=model_bytes, num_threads=2)
        self.interpreter.allocate_tensors()
        self.input = self.interpreter.get_input_details()[0]
        self.output = self.interpreter.get_output_details()[0]
        if (list(self.input['shape']) != [1, meta['networkInputLength'], 28] or
                list(self.output['shape']) != [1, 1] or
                self.input['dtype'] != np.float32 or self.output['dtype'] != np.float32):
            raise ValueError('Tensor CNN harus float32 [1,200,28] -> [1,1]')

    def predict(self, samples):
        np = self.np
        values = np.asarray(samples, dtype=np.float32)
        if values.shape != (INPUT_LENGTH, 28) or not np.isfinite(values).all():
            raise ValueError('Input CNN harus 60 x 28 angka finite')
        count = self.metadata['networkInputLength']
        if count != INPUT_LENGTH:
            values = np.stack([np.interp(np.linspace(0, INPUT_LENGTH - 1, count), np.arange(INPUT_LENGTH), values[:, c])
                               for c in range(28)], axis=1).astype(np.float32)
        tensor = ((values - self.mean) / self.std)[None, ...]
        started = time.perf_counter()
        self.interpreter.set_tensor(self.input['index'], tensor)
        self.interpreter.invoke()
        probability = float(self.interpreter.get_tensor(self.output['index'])[0, 0])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError('Output CNN bukan probabilitas valid')
        return probability, (time.perf_counter() - started) * 1000


class WalkingGate:
    # Same pressure channels/thresholds as Android; tune to the physical sensor calibration.
    LEFT_RANGE = 40.0
    RIGHT_RANGE = 80.0
    STOPPED_RANGE = 35.0

    def __init__(self):
        self.history = deque()
        self.walking = False
        self.evidence_since = None

    def accept(self, timestamp, values):
        self.history.append((timestamp, sum(values[:8]), sum(values[14:22])))
        while self.history and timestamp - self.history[0][0] > 1_000_000:
            self.history.popleft()
        if timestamp - self.history[0][0] < 800_000:
            return False
        ranges, transitions = [], []
        for column in (1, 2):
            pressures = [row[column] for row in self.history]
            low, high = min(pressures), max(pressures)
            ranges.append(high - low)
            states = [p >= low + (high - low) * .35 for p in pressures]
            transitions.append(sum(a != b for a, b in zip(states, states[1:]))
                               if high - low >= self.STOPPED_RANGE else 0)
        evidence = (max(ranges) <= self.STOPPED_RANGE and max(transitions) == 0 if self.walking else
                    ranges[0] >= self.LEFT_RANGE and ranges[1] >= self.RIGHT_RANGE and min(transitions) >= 2)
        if not evidence:
            self.evidence_since = None
        elif self.evidence_since is None:
            self.evidence_since = timestamp
        elif timestamp - self.evidence_since >= (1_000_000 if self.walking else 300_000):
            self.walking = not self.walking
            self.evidence_since = None
        return self.walking


class GaitWindows:
    """Align two device streams on a 30 Hz host monotonic timeline."""
    MAX_BRACKET_US = 80_000
    MAX_RTT_US = 20_000

    def __init__(self):
        self.revision = 0
        self.boots = {}
        self.reset('Menunggu data kedua kaki dan sinkronisasi')

    def reset(self, message):
        self.buffers = {foot: deque(maxlen=400) for foot in ('left', 'right')}
        self.window = deque(maxlen=INPUT_LENGTH)
        self.gate = WalkingGate()
        self.grid = None
        self.stride = 0
        self.message = message
        self.revision += 1

    def add(self, data, rx, clock, sequence_status):
        if sequence_status in ('duplicate', 'out_of_order'):
            return []
        foot = data['foot']
        if data.get('sensor_valid_mask', 255) != 255 or data.get('imu_valid', True) is not True:
            self.reset('Data sensor/IMU invalid; periksa I2C insole')
            return []
        if self.boots.get(foot, data['boot_id']) != data['boot_id']:
            self.reset('ESP restart, menunggu jendela baru')
        self.boots[foot] = data['boot_id']
        if len(data['pressure']) != 8 or data.get('imu') is None or not 20 <= data['fs_hz'] <= 100:
            self.reset('CNN membutuhkan 8 tekanan + 6 IMU per kaki pada 20-100 Hz')
            return []
        if clock:
            if (not 0 <= rx - clock['measured_host_us'] <= 30_000_000 or
                    not 0 <= clock['rtt_us'] <= self.MAX_RTT_US):
                self.reset('Menunggu sync kedua ESP (RTT maksimal 20 ms)')
                return []
            timestamp = data['t_us'] + clock['offset_us']
            if abs(rx - timestamp) > 100_000:
                self.reset('Sampel terlambat / clock tidak cocok (batas 100 ms)')
                return []
        else:
            self.reset('Menunggu sinkronisasi clock; recording satu kaki tetap tersedia')
            return []
        buffer = self.buffers[foot]
        if buffer and (timestamp <= buffer[-1][0] or timestamp - buffer[-1][0] > self.MAX_BRACKET_US):
            self.reset('Celah waktu / perubahan clock, mengisi ulang jendela')
            buffer = self.buffers[foot]
        buffer.append((timestamp, data['pressure'] + data['imu']))
        if any(len(b) < 2 for b in self.buffers.values()):
            return []
        first = max(b[0][0] for b in self.buffers.values())
        if self.grid is None or self.grid < first:
            self.window.clear()
            self.gate = WalkingGate()
            self.stride = 0
            self.revision += 1
            self.grid_index = math.ceil(first * SAMPLING_RATE / 1_000_000)
            self.grid = round(self.grid_index * 1_000_000 / SAMPLING_RATE)
        windows = []
        while self.grid < min(b[-1][0] for b in self.buffers.values()):
            values = []
            for b in self.buffers.values():
                while len(b) > 2 and b[1][0] < self.grid:
                    b.popleft()
                (t0, v0), (t1, v1) = b[0], b[1]
                if not t0 <= self.grid <= t1 or t1 - t0 > self.MAX_BRACKET_US:
                    self.reset('Pasangan waktu tidak valid, mengisi ulang jendela')
                    return []
                weight = (self.grid - t0) / (t1 - t0)
                values.extend(a + weight * (z - a) for a, z in zip(v0, v1))
            if not self.gate.accept(self.grid, values):
                if self.window:
                    self.window.clear()
                    self.stride = 0
                    self.revision += 1
                self.message = 'Menunggu pola berjalan'
            else:
                self.window.append(values)
                self.stride += 1
                self.message = f'Mengisi jendela berjalan: {len(self.window)}/{INPUT_LENGTH}'
                if len(self.window) == INPUT_LENGTH and self.stride >= STRIDE:
                    windows.append((self.grid, list(self.window)))
                    self.stride = 0
            self.grid_index += 1
            self.grid = round(self.grid_index * 1_000_000 / SAMPLING_RATE)
        return windows


class CnnAnalysis:
    def __init__(self):
        self.lock = threading.Lock()
        self.incoming = queue.Queue(maxsize=1000)
        self.results = queue.Queue(maxsize=20)
        self.thread = None
        self.active = False
        self.generation = 0
        self.state = dict(message='Analisis CNN belum dimulai', prediction=None, model=None)

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Worker CNN masih aktif, tunggu lalu coba lagi')
            self.active = True
            self.generation += 1
            self.state.update(message='Memuat model CNN...', prediction=None)
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.active = False
            self.generation += 1
            self.state.update(message='Analisis CNN berhenti', prediction=None)
        if self.thread:
            self.thread.join(timeout=3)
            if self.thread.is_alive():
                raise RuntimeError('Worker CNN masih menyelesaikan inference, ulangi Stop')

    def submit(self, data, rx, clock, status):
        with self.lock:
            if not self.active:
                return
            try:
                self.incoming.put_nowait((self.generation, time.monotonic(), data, rx, clock, status))
            except queue.Full:
                self.generation += 1
                self.state.update(message='Antrean CNN penuh, mengisi ulang jendela', prediction=None)

    def snapshot(self):
        with self.lock:
            state = dict(self.state, active=self.active)
            if state['prediction'] and time.monotonic() - state['prediction']['published_at'] > 1:
                state.update(message='Hasil kedaluwarsa, menunggu data berjalan baru', prediction=None)
            return state

    def drain_results(self):
        results = []
        with self.lock:
            while True:
                try:
                    generation, result = self.results.get_nowait()
                except queue.Empty:
                    return results
                if generation == self.generation and self.active:
                    results.append(result)

    def _run(self):
        try:
            model = GaitModel()
            windows = GaitWindows()
            probabilities = deque(maxlen=10)
            generation = None
            with self.lock:
                self.state['model'] = dict(version=model.metadata['modelVersion'],
                                           purpose=model.metadata['purpose'], sha256=model.sha256,
                                           metadata_sha256=hashlib.sha256((MODEL_DIR / 'gait_test_model.json').read_bytes()).hexdigest(),
                                           source_layout=model.metadata['sourceLayout'],
                                           decision_threshold=model.metadata['decisionThreshold'],
                                           input_shape=[1, INPUT_LENGTH, 28], sampling_rate=SAMPLING_RATE, stride=STRIDE,
                                           network_input_length=model.metadata['networkInputLength'],
                                           averaged_windows=10, max_bracket_us=GaitWindows.MAX_BRACKET_US,
                                           max_sync_rtt_us=GaitWindows.MAX_RTT_US,
                                           walking_thresholds=dict(left=WalkingGate.LEFT_RANGE,
                                                                   right=WalkingGate.RIGHT_RANGE,
                                                                   stopped=WalkingGate.STOPPED_RANGE))
                if self.active:
                    self.state['message'] = windows.message
            while True:
                with self.lock:
                    if not self.active:
                        return
                try:
                    epoch, queued_at, data, rx, clock, status = self.incoming.get(timeout=.1)
                except queue.Empty:
                    continue
                with self.lock:
                    if epoch != self.generation or time.monotonic() - queued_at > .5:
                        continue
                if epoch != generation:
                    windows.reset('Menunggu data kedua kaki dan sinkronisasi')
                    probabilities.clear()
                    generation = epoch
                revision = windows.revision
                ready = windows.add(data, rx, clock, status)
                if revision != windows.revision:
                    probabilities.clear()
                with self.lock:
                    if epoch == self.generation:
                        self.state['message'] = windows.message
                        if not probabilities:
                            self.state['prediction'] = None
                for end, samples in ready:
                    probability, latency = model.predict(samples)
                    probabilities.append(probability)
                    average = sum(probabilities) / len(probabilities)
                    abnormal = average >= model.metadata['decisionThreshold']
                    result = dict(window_start_us=end - round((INPUT_LENGTH - 1) * 1_000_000 / SAMPLING_RATE), window_end_us=end,
                                  label='ABNORMAL' if abnormal else 'NORMAL',
                                  confidence=average if abnormal else 1 - average,
                                  abnormal_probability=average, window_probability=probability,
                                  inference_ms=latency, averaged_windows=len(probabilities),
                                  published_at=time.monotonic(), model_version=model.metadata['modelVersion'])
                    with self.lock:
                        if not self.active or epoch != self.generation:
                            continue
                        self.state.update(message='Klasifikasi berjalan', prediction=result)
                        if self.results.full():
                            self.results.get_nowait()
                        self.results.put_nowait((epoch, result))
        except Exception as error:
            with self.lock:
                self.active = False
                hint = ' Instal requirements.txt.' if isinstance(error, ImportError) else ''
                self.state.update(message=f'CNN tidak tersedia: {error}.{hint}',
                                  prediction=None)
