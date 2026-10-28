"""Native Tk smoke test; requires a desktop session, no third-party libraries."""
import json
import importlib.util
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

from app import App
from simulator import Simulator


class DesktopTest(unittest.TestCase):
    def test_buttons_record_and_close_session(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Desktop unavailable: {error}')
        root.withdraw()
        application = App(root)
        with tempfile.TemporaryDirectory() as directory:
            try:
                application.output.set(directory)
                application.subject.set('DEMO-TEST')
                application.label.set('SIMULATED')
                port = application.receiver.start('127.0.0.1', 0)
                application.simulator = Simulator('127.0.0.1', port)
                application.simulator.start()
                deadline = time.monotonic() + 4
                while len(application.receiver.snapshot()['devices']) != 2:
                    self.assertLess(time.monotonic(), deadline)
                    root.update()
                    time.sleep(0.02)
                # Process a refresh to update button states before invoking them.
                for _ in range(15):
                    root.update()
                    time.sleep(0.02)
                application.record.invoke()
                self.assertIsNotNone(application.receiver.recording)
                path = Path(application.receiver.last_path)
                if importlib.util.find_spec('ai_edge_litert'):
                    application.analyze.invoke()
                    deadline = time.monotonic() + 12
                    while application.cnn.snapshot()['prediction'] is None:
                        self.assertLess(time.monotonic(), deadline, application.cnn_status.get())
                        root.update()
                        time.sleep(0.02)
                    application.refresh()
                    self.assertIn('Confidence', application.cnn_status.get())
                for _ in range(20):
                    root.update()
                    time.sleep(0.02)
                self.assertGreater(application.receiver.recording.counts['left'], 0)
                application.close()
                self.assertEqual(json.loads((path / 'metadata.json').read_text())['status'], 'complete')
                self.assertIsNone(application.receiver.sock)
            finally:
                if application.simulator:
                    application.simulator.stop()
                application.receiver.stop()
                try:
                    root.destroy()
                except tk.TclError:
                    pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
