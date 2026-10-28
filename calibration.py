"""Session calibration and display filtering. Original packets are never changed."""
import copy
import math
import statistics
import threading
from collections import deque

from acquisition import now_us, utc_now

PRESSURE_DIVISOR = 2000.0
FILTER_TAU_SECONDS = .06
ACCEL_LSB_PER_G = 16384.0  # Current BMI160 ACC_RANGE=0x03 (+/-2g).


class SensorMonitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.profiles = {'pressure': {}, 'imu': {}}
        self.capture = None
        self.message = ''
        self.identities = {}
        self.history = {foot: deque(maxlen=300) for foot in ('left', 'right')}
        self.filters = {}

    def begin(self, kind, devices, duration=None):
        with self.lock:
            if kind not in self.profiles:
                raise ValueError('Jenis kalibrasi tidak dikenal')
            if self.capture:
                raise ValueError('Tunggu kalibrasi yang sedang berjalan')
            targets = {foot: (d['device_id'], d['latest']['boot_id']) for foot, d in devices.items() if d['age_s'] < .5}
            if not targets:
                raise ValueError('Belum ada insole yang mengirim data segar')
            for foot in targets:
                packet = devices[foot]['latest']
                if packet.get('sensor_valid_mask', 255) != 255:
                    raise ValueError(f'Sensor {foot} tidak lengkap. Periksa sambungan I2C.')
                if kind == 'pressure' and len(packet.get('raw') or []) != 8:
                    raise ValueError('Firmware harus mengirim 8 raw FDC untuk baseline tekanan')
                if kind == 'imu' and (not packet.get('imu') or not packet.get('imu_valid', True)):
                    raise ValueError('IMU belum mengirim data valid')
            started = now_us()
            self.capture = dict(kind=kind, targets=targets, started=started,
                                duration=duration if duration is not None else (10 if kind == 'pressure' else 3),
                                samples={foot: [] for foot in targets}, last={foot: started for foot in targets},
                                rates={foot: devices[foot]['latest']['fs_hz'] for foot in targets})
            self.message = 'Lepaskan beban dan diamkan insole' if kind == 'pressure' else 'Diamkan insole; posisi tidak berubah'

    def _fail(self, message):
        self.capture = None
        self.message = message

    def _finish(self, current):
        capture = self.capture
        if not capture:
            return
        if any(current - last > 500_000 for last in capture['last'].values()):
            self._fail('Kalibrasi gagal: data terputus. Ulangi setelah tersambung.')
            return
        if current - capture['started'] < capture['duration'] * 1e6:
            return
        profiles = {}
        for foot, samples in capture['samples'].items():
            minimum = max(3, int(capture['duration'] * capture['rates'][foot] * .7))
            if len(samples) < minimum:
                self._fail('Kalibrasi gagal: sampel belum cukup. Ulangi.')
                return
            columns = list(zip(*samples))
            spread = [statistics.pstdev(column) for column in columns]
            if capture['kind'] == 'pressure':
                baseline = [statistics.median(column) for column in columns]
                if any(b <= 0 or s/b > .001 for b, s in zip(baseline, spread)):
                    self._fail('Baseline tidak stabil. Lepaskan beban dan diamkan kabel, lalu ulangi.')
                    return
                profile = dict(baseline=baseline, noise_std=spread, divisor=PRESSURE_DIVISOR)
            else:
                means = [statistics.mean(column) for column in columns]
                gravity = math.sqrt(sum(v*v for v in means[:3])) / ACCEL_LSB_PER_G
                if max(spread[:3]) > 180 or max(spread[3:]) > 50 or not .8 <= gravity <= 1.2:
                    self._fail('IMU belum diam / skala tidak sesuai. Letakkan insole diam lalu ulangi.')
                    return
                profile = dict(gyro_bias=means[3:], accel_reference=means[:3], noise_std=spread,
                               accel_lsb_per_g=ACCEL_LSB_PER_G, accelerometer_offset_corrected=False)
            device, boot = capture['targets'][foot]
            profiles[foot] = dict(profile, device_id=device, boot_id=boot, captured_utc=utc_now(), samples=len(samples))
        self.profiles[capture['kind']].update(profiles)
        self.filters.clear()
        self.message = 'Baseline tekanan tersimpan' if capture['kind'] == 'pressure' else 'Bias giroskop tersimpan'
        self.capture = None

    def observe(self, packet, rx, clock, status):
        with self.lock:
            if status in ('duplicate', 'out_of_order'):
                return {}
            foot = packet['foot']
            identity = (packet['device_id'], packet['boot_id'])
            if self.identities.get(foot, identity) != identity:
                for profiles in self.profiles.values():
                    profiles.pop(foot, None)
                self.history[foot].clear()
                self.filters.pop(foot, None)
                self.message = 'Perangkat restart / berubah. Kalibrasi ulang insole tersebut.'
            self.identities[foot] = identity
            capture = self.capture
            if capture and foot in capture['targets']:
                valid = packet.get('sensor_valid_mask', 255) == 255
                values = packet.get('raw') if capture['kind'] == 'pressure' else packet.get('imu')
                expected = 8 if capture['kind'] == 'pressure' else 6
                if (capture['targets'][foot] != identity or not valid or not values or len(values) != expected or
                        (capture['kind'] == 'pressure' and any(not 0 < v < 0xfffffff for v in values)) or
                        (capture['kind'] == 'imu' and not packet.get('imu_valid', True))):
                    self._fail('Kalibrasi gagal: perangkat atau data sensor berubah. Profil lama tetap tersimpan.')
                else:
                    capture['samples'][foot].append(list(values))
                    capture['last'][foot] = rx
            self._finish(rx)
            pressure_profile = self.profiles['pressure'].get(foot)
            imu_profile = self.profiles['imu'].get(foot)
            mask = packet.get('sensor_valid_mask', 255)
            calibrated = None
            if pressure_profile:
                calibrated = ([(b-v)/pressure_profile['divisor'] if mask & (1 << i) and 0 < v < 0xfffffff else None
                               for i, (b, v) in enumerate(zip(pressure_profile['baseline'], packet['raw']))]
                              if len(packet.get('raw') or []) == 8 else [None]*8)
            response = calibrated if calibrated is not None else packet['pressure']
            state = self.filters.get(foot)
            if not state or rx-state['time'] > 200_000:
                state = dict(time=rx, values=list(response), windows=[deque(maxlen=3) for _ in range(8)])
                self.filters[foot] = state
            alpha = -math.expm1(-max(0, rx-state['time'])/1e6/FILTER_TAU_SECONDS)
            filtered = []
            for i, value in enumerate(response):
                if not mask & (1 << i) or value is None:
                    state['windows'][i].clear()
                    filtered.append(None)
                    continue
                window = state['windows'][i]
                first = not window
                window.append(value)
                middle = statistics.median(window)
                state['values'][i] = middle if first else state['values'][i] + alpha*(middle-state['values'][i])
                filtered.append(state['values'][i])
            state['time'] = rx
            imu = packet.get('imu')
            corrected = list(imu[:3]) + [v-b for v, b in zip(imu[3:], imu_profile['gyro_bias'])] if imu and imu_profile and packet.get('imu_valid', True) else None
            self.history[foot].append(dict(t=rx/1e6, original=[v if mask & (1<<i) else None for i,v in enumerate(packet['pressure'])],
                                           values=filtered, imu=corrected or imu))
            return dict(calibrated_pressure=calibrated, filtered_pressure=filtered, corrected_imu=corrected)

    def snapshot(self):
        with self.lock:
            self._finish(now_us())
            capture = self.capture
            return dict(profiles=copy.deepcopy(self.profiles), message=self.message,
                        active=capture['kind'] if capture else None,
                        progress=min(1, (now_us()-capture['started'])/1e6/capture['duration']) if capture else 0,
                        targets=list(capture['targets']) if capture else [],
                        history={foot: list(points)[::2] for foot, points in self.history.items()})
