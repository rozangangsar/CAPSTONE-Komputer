import csv
import json
import socket
import tempfile
import time
import unittest
from pathlib import Path

from acquisition import Receiver, now_us, parse_packet
from simulator import Simulator


def sample(foot='left', seq=0, boot='boot-a'):
    return dict(v=1, type='sample', foot=foot, device_id=foot, boot_id=boot,
                seq=seq, sample_index=seq, t_us=10000 * seq,
                fs_hz=100, pressure=list(range(8)), imu=None)


def wait_for(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError('Timed out waiting for receiver')


class AcquisitionTests(unittest.TestCase):
    def test_protocol_rejects_bad_values(self):
        self.assertEqual(parse_packet(json.dumps(sample()).encode())['foot'], 'left')
        for field, bad in [('v', True), ('seq', -1), ('seq', True), ('foot', 'other'),
                           ('fs_hz', 0), ('pressure', [0]*7), ('pressure', [0]*16),
                           ('pressure', [float('nan')]*8), ('pressure', [10**1000]*8),
                           ('imu', [0]*5), ('device_id', '../x')]:
            with self.subTest(field=field, bad=str(bad)[:30]):
                with self.assertRaises(ValueError):
                    parse_packet(json.dumps(dict(sample(), **{field: bad})).encode())

    def test_sequence_restart_and_sender_conflict(self):
        rx = Receiver()
        address = ('127.0.0.1', 12000)
        for seq in (0, 2, 1, 2):
            rx._handle(sample(seq=seq), address, now_us())
        self.assertEqual(rx.stats['gaps'], 1)
        self.assertEqual(rx.stats['out_of_order'], 1)
        self.assertEqual(rx.stats['duplicates'], 1)
        rx._handle(sample(seq=3), ('127.0.0.1', 12001), now_us())
        self.assertEqual(rx.stats['rejected'], 1)
        rx._handle(sample(seq=0, boot='boot-b'), address, now_us())
        self.assertEqual(rx.stats['restarts'], 1)
        self.assertIsNone(rx.devices['left']['clock'])

    def test_sync_uses_four_timestamps_and_rejects_wrong_nonce(self):
        rx = Receiver()
        address = ('127.0.0.1', 12000)
        rx._handle(sample(), address, 1000)
        dev = rx.devices['left']
        rx.pending['a'] = dict(h1=1000, address=address, foot='left', boot_id='boot-a')
        reply = dict(v=1, type='sync_reply', foot='left', device_id='left',
                     boot_id='boot-a', nonce='a', e2_us=200, e3_us=210)
        rx._handle(reply, address, 1030)
        self.assertEqual(dev['clock']['offset_us'], 810)
        self.assertEqual(dev['clock']['rtt_us'], 20)
        rx._handle(reply, address, 1040)
        self.assertEqual(rx.stats['rejected'], 1)

    def test_udp_two_feet_recording_sync_and_recovery(self):
        rx = Receiver()
        sim = None
        with tempfile.TemporaryDirectory() as directory:
            try:
                port = rx.start('127.0.0.1', 0)
                with self.assertRaises(ValueError):
                    rx.start_recording(directory, 'S001', 'walking', '')
                sim = Simulator('127.0.0.1', port, hz=100, drop_every=10)
                sim.start()
                wait_for(lambda: len(rx.snapshot()['devices']) == 2)
                path = Path(rx.start_recording(directory, 'S001', 'SIMULATED', 'test'))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.sendto(b'invalid JSON', ('127.0.0.1', port))
                wait_for(lambda: all(d['clock'] for d in rx.snapshot()['devices'].values()))
                wait_for(lambda: rx.snapshot()['counts'].get('left', 0) >= 100)
                rx.stop_recording()
                self.assertIsNone(sim.error)
                meta = json.loads((path / 'metadata.json').read_text())
                self.assertEqual(meta['status'], 'complete')
                self.assertGreater(meta['counters']['gaps'], 0)
                self.assertGreater(meta['counters']['invalid'], 0)
                for foot in ('left', 'right'):
                    with (path / f'{foot}.csv').open(newline='') as file:
                        rows = list(csv.DictReader(file))
                    self.assertEqual(len(rows), meta['sample_rows'][foot])
                    self.assertGreater(len(rows), 50)
                    self.assertTrue(any(row['estimated_host_sample_us'] for row in rows))
                    self.assertTrue(all(row['esp_t_us'] for row in rows))
                    self.assertTrue(all(None not in row for row in rows))
                packets = [json.loads(line) for line in (path / 'packets.jsonl').read_text().splitlines()]
                replies = [p for p in packets if p['packet']['type'] == 'sync_reply']
                self.assertTrue(replies)
                self.assertIn('h1_us', replies[0]['packet'])
                second = Path(rx.start_recording(directory, 'S002', 'SIMULATED', ''))
                self.assertNotEqual(second, path)
                rx.stop()
                self.assertEqual(json.loads((second / 'metadata.json').read_text())['status'], 'complete')
            finally:
                if sim:
                    sim.stop()
                rx.stop()

    def test_disk_failure_stops_recording(self):
        rx = Receiver()
        with tempfile.TemporaryDirectory() as directory:
            rx._handle(sample(), ('127.0.0.1', 10000), now_us())
            rx._handle(sample('right'), ('127.0.0.1', 10001), now_us())
            from acquisition import Recording
            rx.recording = Recording(directory, 'S001', 'test', '', {})
            path = rx.recording.path
            rx.recording.raw.close()
            with self.assertRaises(ValueError):
                rx._handle(sample(seq=1), ('127.0.0.1', 10000), now_us())
            rx.stop_recording('error')
            self.assertEqual(json.loads((path / 'metadata.json').read_text())['status'], 'error')


if __name__ == '__main__':
    unittest.main(verbosity=2)
