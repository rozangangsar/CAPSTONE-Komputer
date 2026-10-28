import json
import unittest
from acquisition import parse_packet
from test_acquisition import sample

class SensorProfilesTest(unittest.TestCase):
    def test_default_eight_and_explicit_trial_four(self):
        packet = sample()
        self.assertEqual(len(parse_packet(json.dumps(packet).encode())['pressure']), 8)
        packet['pressure'] = [1, 2, 3, 4]
        with self.assertRaises(ValueError):
            parse_packet(json.dumps(packet).encode())
        self.assertEqual(len(parse_packet(json.dumps(packet).encode(), 4)['pressure']), 4)
