import csv
import importlib.util
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from acquisition import Receiver
from cnn import CHANNELS, CnnAnalysis, GaitModel, GaitWindows, MODEL_DIR
from simulator import Simulator
from test_acquisition import sample, wait_for


def feed(builder, start=0, end=500, missing_right=None):
    windows = []
    for index in range(start, end):
        for foot, offset in (('left', 3_000_000), ('right', 9_000_000)):
            if foot == 'right' and index == missing_right:
                continue
            phase = index / 30 * 2 * math.pi + (math.pi if foot == 'right' else 0)
            packet = sample(foot, index)
            packet.update(t_us=offset + round(index * 1_000_000 / 30), fs_hz=30,
                          pressure=[100 + max(0, math.sin(phase)) * 800] * 8,
                          imu=[index, 2, 3, 4, 5, 6])
            rx = 1_000_000 + round(index * 1_000_000 / 30)
            clock = dict(offset_us=1_000_000 - offset, measured_host_us=rx, rtt_us=100)
            windows.extend(builder.add(packet, rx, clock, 'in_order'))
    return windows


class WindowTests(unittest.TestCase):
    def test_window_grid_channels_stride_and_missing_sample(self):
        builder = GaitWindows()
        windows = feed(builder, missing_right=250)
        self.assertGreater(len(windows), 1)
        for end, values in windows:
            self.assertEqual(len(values), 60)
            self.assertEqual(len(values[0]), 28)
            self.assertLessEqual(abs(end - round(round(end * 30 / 1_000_000) * 1_000_000 / 30)), 1)
            self.assertAlmostEqual(values[-1][8], (end - 1_000_000) * 30 / 1_000_000, places=3)
            self.assertEqual(values[-1][8:14], values[-1][22:28])
        self.assertTrue(all(b[0] - a[0] == 500_000 for a, b in zip(windows, windows[1:])))

    def test_invalid_inputs_and_restart_clear_windows(self):
        builder = GaitWindows()
        self.assertTrue(feed(builder))
        for changes, clock in [({}, None), ({'imu': None}, {}), ({'pressure': [1]*4}, {}),
                               ({'fs_hz': 50}, {}), ({}, dict(offset_us=0, measured_host_us=0, rtt_us=100))]:
            packet = dict(sample(seq=500), imu=[0]*6)
            packet.update(changes)
            self.assertEqual(builder.add(packet, 40_000_000, clock, 'in_order'), [])
            self.assertFalse(builder.window)
        builder = GaitWindows()
        feed(builder)
        previous = list(builder.window)
        self.assertEqual(builder.add(sample(), 0, None, 'duplicate'), [])
        self.assertEqual(list(builder.window), previous)
        reboot = dict(sample(boot='new-boot'), imu=[0]*6, t_us=0)
        builder.add(reboot, 10_000_000, dict(offset_us=10_000_000, measured_host_us=10_000_000, rtt_us=100), 'restart')
        self.assertFalse(builder.window)
        self.assertFalse(builder.buffers['right'])

    def test_large_gap_and_standing_require_new_walking_window(self):
        builder = GaitWindows()
        self.assertTrue(feed(builder))
        self.assertFalse(feed(builder, start=600, end=601))
        self.assertFalse(builder.window)
        builder = GaitWindows()
        clock = dict(offset_us=1_000_000, measured_host_us=1_000_000, rtt_us=100)
        for index in range(400):
            for foot in ('left', 'right'):
                packet = dict(sample(foot, index), imu=[0]*6)
                self.assertFalse(builder.add(packet, 1_000_000 + index * 10_000, clock, 'in_order'))
        self.assertEqual(builder.message, 'Menunggu pola berjalan')

    def test_rejects_delayed_packets_and_high_rtt(self):
        for clock, rx in [(dict(offset_us=0, measured_host_us=1_000_000, rtt_us=100), 1_000_000),
                          (dict(offset_us=1_000_000, measured_host_us=1_000_000, rtt_us=20_001), 1_000_000)]:
            builder = GaitWindows()
            packet = dict(sample(), imu=[0]*6)
            self.assertFalse(builder.add(packet, rx, clock, 'in_order'))
            self.assertFalse(builder.buffers['left'])

    def test_model_error_is_visible_and_does_not_stop_receiver(self):
        analysis = CnnAnalysis()
        with patch('cnn.GaitModel', side_effect=RuntimeError('bad model')):
            analysis.start()
            wait_for(lambda: not analysis.snapshot()['active'])
        self.assertIn('bad model', analysis.snapshot()['message'])
        self.assertIsNone(analysis.snapshot()['prediction'])
        analysis.stop()


@unittest.skipUnless(importlib.util.find_spec('ai_edge_litert'), 'Install requirements.txt for real CNN checks')
class RealModelTests(unittest.TestCase):
    def test_android_normalization_and_output(self):
        model = GaitModel()
        self.assertEqual(model.metadata['channelNames'], CHANNELS)
        self.assertEqual(model.metadata['purpose'], 'FUNCTIONAL_TEST_ONLY')
        probability, latency = model.predict([model.mean.tolist()] * 60)
        import numpy as np
        model.interpreter.set_tensor(model.input['index'], np.zeros((1, 200, 28), dtype=np.float32))
        model.interpreter.invoke()
        expected = float(model.interpreter.get_tensor(model.output['index'])[0, 0])
        self.assertAlmostEqual(probability, expected, places=6)
        self.assertTrue(0 <= probability <= 1)
        self.assertGreaterEqual(latency, 0)
        with self.assertRaises(ValueError):
            model.predict([[float('nan')]*28]*60)

    def test_udp_classification_recording_and_restart(self):
        analysis = CnnAnalysis()
        receiver = Receiver(analysis=analysis)
        simulator = None
        with tempfile.TemporaryDirectory() as directory:
            try:
                port = receiver.start('127.0.0.1', 0)
                analysis.start()
                simulator = Simulator('127.0.0.1', port, hz=30)
                simulator.start()
                wait_for(lambda: analysis.snapshot()['model'] is not None)
                wait_for(lambda: len(receiver.snapshot()['devices']) == 2)
                path = Path(receiver.start_recording(directory, 'TEST', 'SIMULATED', ''))
                wait_for(lambda: analysis.snapshot()['prediction'] is not None, timeout=12)
                wait_for(lambda: receiver.recording.prediction_count > 0)
                prediction = analysis.snapshot()['prediction']
                self.assertIn(prediction['label'], ('NORMAL', 'ABNORMAL'))
                self.assertAlmostEqual(prediction['confidence'], max(prediction['abnormal_probability'], 1 - prediction['abnormal_probability']))
                receiver.stop_recording()
                with (path / 'predictions.csv').open(newline='') as file:
                    rows = list(csv.DictReader(file))
                self.assertTrue(rows)
                self.assertEqual(json.loads((path / 'metadata.json').read_text())['cnn']['purpose'], 'FUNCTIONAL_TEST_ONLY')
                with (path / 'left.csv').open(newline='') as file:
                    header = next(csv.reader(file))
                self.assertIn('p8', header)
                self.assertNotIn('p9', header)
                receiver.stop()
                self.assertIsNone(analysis.snapshot()['prediction'])
                analysis.start()
                wait_for(lambda: 'Memuat' not in analysis.snapshot()['message'])
                self.assertIsNone(analysis.snapshot()['prediction'])
                self.assertFalse(analysis.drain_results())
            finally:
                if simulator:
                    simulator.stop()
                receiver.stop()


if __name__ == '__main__':
    unittest.main(verbosity=2)
