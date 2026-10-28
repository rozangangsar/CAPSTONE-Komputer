import csv
import json
import socket
import tempfile
import unittest
from pathlib import Path
from acquisition import Receiver, parse_packet
from test_acquisition import sample, wait_for
from cnn import GaitWindows


class CurrentHardwareTests(unittest.TestCase):
    def test_invalid_hardware_never_reaches_classifier_window(self):
        builder=GaitWindows()
        packet=dict(sample('left',1),imu=[0]*6,fs_hz=30,sensor_valid_mask=0)
        self.assertEqual(builder.add(packet,1000000,dict(offset_us=0,measured_host_us=1000000,rtt_us=100),'in_order'), [])
        self.assertIn('invalid',builder.message)
        self.assertFalse(builder.buffers['left'])
    def test_single_foot_recording_and_canonical_imu_raw(self):
        receiver = Receiver()
        with tempfile.TemporaryDirectory() as root, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            try:
                port = receiver.start('127.0.0.1', 0)
                for seq in range(2):
                    pkt = dict(sample('right', seq), fs_hz=30, raw=[28000000+i for i in range(8)], imu=[1,2,3,100,200,16000])
                    sender.sendto(json.dumps(pkt).encode(), ('127.0.0.1', port))
                wait_for(lambda: 'right' in receiver.snapshot()['devices'])
                path = Path(receiver.start_recording(root, 'TEST', 'UNLABELLED', 'single-foot hardware test'))
                pkt['seq']=3; pkt['sample_index']=3
                sender.sendto(json.dumps(pkt).encode(), ('127.0.0.1', port))
                wait_for(lambda: receiver.recording.counts['right']>0)
                receiver.stop_recording()
                meta=json.loads((path/'metadata.json').read_text())
                self.assertEqual(meta['recording_sides'], ['right'])
                self.assertEqual(meta['sample_rows']['left'], 0)
                with (path/'right.csv').open(newline='') as f: row=next(csv.DictReader(f))
                self.assertEqual(row['ax'], '100')
                self.assertEqual(row['gx'], '1')
                self.assertEqual(row['raw8'], '28000007')
            finally: receiver.stop()

    def test_single_foot_never_builds_fake_bilateral_window(self):
        builder=GaitWindows()
        for seq in range(150):
            packet=dict(sample('right',seq),fs_hz=30,imu=[0]*6,t_us=seq*33333)
            self.assertEqual(builder.add(packet,1_000_000+seq*33333,dict(offset_us=1_000_000,measured_host_us=1_000_000,rtt_us=100),'in_order'), [])

if __name__=='__main__': unittest.main()
