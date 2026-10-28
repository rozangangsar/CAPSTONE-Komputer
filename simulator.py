"""Two synthetic ESP streams with independent clocks and SYNC replies."""
import argparse
import json
import math
import select
import socket
import threading
import time
import uuid


class Simulator:
    def __init__(self, host='127.0.0.1', port=9000, hz=100, drop_every=0):
        if not 1 <= hz <= 1000 or drop_every < 0:
            raise ValueError('hz must be 1..1000; drop_every must be nonnegative')
        self.target = (host, port)
        self.hz = hz
        self.drop_every = drop_every
        self.stopping = threading.Event()
        self.thread = None
        self.sockets = []
        self.sent = 0
        self.error = None
        self.motion = 'walking'

    def start(self):
        if self.thread:
            raise ValueError('Simulator already started')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        start = time.perf_counter()
        offsets = (3_000_000, 9_000_000)
        drifts = (1.00002, 0.99997)
        boots = [uuid.uuid4().hex[:12] for _ in range(2)]

        def timestamp(index):
            return int((time.perf_counter() - start) * 1e6 * drifts[index]) + offsets[index]

        try:
            for _ in range(2):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(('0.0.0.0', 0))
                sock.setblocking(False)
                self.sockets.append(sock)
            seq = 0
            due = start
            while not self.stopping.is_set():
                current = time.perf_counter()
                if current >= due:
                    for i, foot in enumerate(('left', 'right')):
                        if self.drop_every and seq and seq % self.drop_every == 0 and i == 1:
                            continue
                        phase = (current - start) * 2 * math.pi + i * math.pi
                        contact = max(0, math.sin(phase)) if self.motion == 'walking' else 0
                        pressure = [round(100 + contact * (800 + j * 35), 2) for j in range(8)]
                        sample = dict(v=1, type='sample', foot=foot,
                                      device_id=f'demo-{foot}', boot_id=boots[i],
                                      seq=seq, sample_index=seq, t_us=timestamp(i), fs_hz=self.hz,
                                      pressure=pressure, raw=[round(28_000_000-p*2000) for p in pressure],
                                      sensor_valid_mask=255, imu_valid=True,
                                      imu=[0, round(100*math.cos(phase)) if self.motion == 'walking' else 0, 0,
                                           round(1000*math.sin(phase)) if self.motion == 'walking' else 0, 0, 16384])
                        self.sockets[i].sendto(json.dumps(sample).encode(), self.target)
                        self.sent += 1
                    seq += 1
                    due = start + seq / self.hz
                    if current - due > 0.2:
                        # Simulator is approximate; never fake a burst of historical sensor reads.
                        seq = int((current - start) * self.hz) + 1
                        due = start + seq / self.hz
                ready, _, _ = select.select(self.sockets, [], [], max(0, min(due - time.perf_counter(), 0.02)))
                for sock in ready:
                    raw, source = sock.recvfrom(4096)
                    i = self.sockets.index(sock)
                    e2 = timestamp(i)
                    try:
                        request = json.loads(raw)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(request, dict) or request.get('type') != 'sync':
                        continue
                    foot = ('left', 'right')[i]
                    reply = dict(v=1, type='sync_reply', foot=foot,
                                 device_id=f'demo-{foot}', boot_id=boots[i],
                                 nonce=request.get('nonce'), e2_us=e2, e3_us=timestamp(i))
                    sock.sendto(json.dumps(reply).encode(), source)
        except Exception as error:
            self.error = str(error)
        finally:
            for sock in self.sockets:
                sock.close()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--hz', type=int, default=100)
    parser.add_argument('--drop-every', type=int, default=0,
                        help='Skip each Nth right sample for gap testing')
    parser.add_argument('--seconds', type=float, default=0, help='0 means until Ctrl+C')
    args = parser.parse_args()
    simulator = Simulator(args.host, args.port, args.hz, args.drop_every)
    simulator.start()
    print(f'SIMULATED left + right → {args.host}:{args.port}, {args.hz} Hz per foot. Ctrl+C to stop.')
    deadline = time.monotonic() + args.seconds if args.seconds > 0 else float('inf')
    try:
        while time.monotonic() < deadline and simulator.thread.is_alive():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        simulator.stop()
    if simulator.error:
        raise SystemExit(simulator.error)
    print(f'Sent {simulator.sent} datagrams.')
