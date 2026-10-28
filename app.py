"""Smart Insole desktop collector. Run with py -3.11 app.py."""
import os
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from acquisition import Receiver
from cnn import CnnAnalysis
from sensor_contract import SENSOR_LAYOUT


class App:
    def __init__(self, root):
        self.root = root
        self.cnn = CnnAnalysis()
        self.receiver = Receiver(analysis=self.cnn)
        self.simulator = None
        self.error_shown = ''
        root.title('Smart Insole | Research acquisition')
        root.geometry('1000x850')
        root.minsize(820, 800)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Title.TLabel', font=('Segoe UI', 19, 'bold'))
        style.configure('Heading.TLabel', font=('Segoe UI', 11, 'bold'))
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Smart Insole · Akuisisi riset', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='Dua ESP → UDP lokal → dataset untuk training di BRIN').pack(anchor='w', pady=(2, 14))
        conn = ttk.LabelFrame(outer, text='Penerima UDP', padding=10)
        conn.pack(fill='x')
        self.host = tk.StringVar(value='0.0.0.0')
        self.port = tk.StringVar(value='9000')
        self.host_entry = self.field(conn, 'Bind IP', self.host, 0, width=18)
        self.port_entry = self.field(conn, 'Port', self.port, 2, width=8)
        self.profile = tk.StringVar(value='8')
        self.profile_entry = ttk.Combobox(conn, textvariable=self.profile, values=('8', '4'), state='readonly', width=8)
        self.profile_entry.grid(row=2, column=1)
        ttk.Label(conn, text='Sensor per kaki: 8 kapasitif, 4 legacy').grid(row=2, column=2, columnspan=3)
        self.listen = ttk.Button(conn, text='Mulai menerima', command=self.toggle_listener)
        self.listen.grid(row=0, column=4, padx=10)
        ttk.Label(conn, text='ESP mengirim ke IP LAN laptop, bukan ke 0.0.0.0.').grid(row=1, column=0, columnspan=5, sticky='w', pady=(8, 0))
        self.connection = tk.StringVar(value='Belum menerima data')
        ttk.Label(outer, textvariable=self.connection, wraplength=850).pack(anchor='w', pady=10)

        panels = ttk.Frame(outer)
        panels.pack(fill='both', expand=True)
        panels.columnconfigure((0, 1), weight=1)
        panels.rowconfigure(0, weight=1)
        self.foot_ui = {}
        self.imu_ui = {}
        for column, foot in enumerate(('left', 'right')):
            frame = ttk.LabelFrame(panels, text='Kaki kiri' if foot == 'left' else 'Kaki kanan', padding=10)
            frame.grid(row=0, column=column, sticky='nsew', padx=(0, 6) if column == 0 else (6, 0))
            status = tk.StringVar(value='Menunggu ESP…')
            sync = tk.StringVar(value='Clock belum terukur')
            ttk.Label(frame, textvariable=status, wraplength=370).pack(anchor='w')
            ttk.Label(frame, textvariable=sync, wraplength=370).pack(anchor='w', pady=(4, 8))
            tree = ttk.Treeview(frame, columns=('sensor', 'value', 'sensor2', 'value2'), show='headings', height=4)
            for key, title in zip(tree['columns'], ('Kanal', 'Respons', 'Kanal', 'Respons')):
                tree.heading(key, text=title)
                tree.column(key, width=65, anchor='center', stretch=True)
            for i in range(4):
                tree.insert('', 'end', iid=str(i), values=(f'P{i+1}', '—', f'P{i+5}', '—'))
            tree.pack(fill='both', expand=True)
            self.imu_ui[foot] = tk.StringVar(value='IMU: menunggu data')
            ttk.Label(frame, textvariable=self.imu_ui[foot], wraplength=370).pack(anchor='w', pady=6)
            self.foot_ui[foot] = status, sync, tree

        analysis = ttk.LabelFrame(outer, text='Klasifikasi gait 1D CNN', padding=10)
        analysis.pack(fill='x', pady=(12, 0))
        self.analyze = ttk.Button(analysis, text='Mulai analisis CNN', command=self.toggle_analysis)
        self.analyze.pack(anchor='w')
        self.cnn_status = tk.StringVar(value='Analisis CNN belum dimulai')
        ttk.Label(analysis, textvariable=self.cnn_status, wraplength=900).pack(anchor='w', pady=4)
        ttk.Label(analysis, text='MODEL UJI dari HP | NORMAL / ABNORMAL | Bukan diagnosis medis',
                  wraplength=900).pack(anchor='w')

        form = ttk.LabelFrame(outer, text='Rekam sesi', padding=10)
        form.pack(fill='x', pady=(14, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self.subject = tk.StringVar(value='S001')
        self.label = tk.StringVar(value='NORMAL')
        self.notes = tk.StringVar()
        self.output = tk.StringVar(value=str(Path(__file__).resolve().parent / 'recordings'))
        self.subject_entry = self.field(form, 'ID subjek', self.subject, 0)
        self.label_entry = self.field(form, 'Label ground truth', self.label, 2)
        self.note_entry = self.field(form, 'Catatan / unit sensor', self.notes, 0, row=1, width=50, span=3)
        self.output_entry = self.field(form, 'Folder dataset', self.output, 0, row=2, width=50, span=2)
        self.browse = ttk.Button(form, text='Pilih folder', command=self.choose_folder)
        self.browse.grid(row=2, column=3, padx=5, pady=4)
        actions = ttk.Frame(form)
        actions.grid(row=3, column=0, columnspan=4, sticky='ew', pady=(8, 0))
        self.record = ttk.Button(actions, text='Mulai rekam', command=self.toggle_recording)
        self.record.pack(side='left')
        ttk.Button(actions, text='Buka hasil terakhir', command=self.open_results).pack(side='left', padx=8)
        self.session = tk.StringVar(value='Satu kaki bisa direkam; CNN bilateral membutuhkan kedua kaki.')
        ttk.Label(outer, textvariable=self.session, wraplength=900).pack(anchor='w', pady=(10, 4))
        ttk.Label(outer, text='8 sensor + 6 IMU per kaki • 30 Hz / 60 sampel per jendela • model lama memakai adapter internal • rekam satu kaki tersedia', wraplength=900).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()

    @staticmethod
    def field(parent, label, variable, column, row=0, width=20, span=1):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky='w', padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=column+1, columnspan=span, sticky='ew', padx=(0, 8), pady=4)
        return entry

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output.get())
        if folder:
            self.output.set(folder)

    def toggle_listener(self):
        try:
            if self.receiver.sock is not None:
                self.receiver.stop()
            else:
                port = int(self.port.get())
                if not 1 <= port <= 65535:
                    raise ValueError('Port harus 1..65535')
                self.receiver.sensor_count = int(self.profile.get())
                self.receiver.start(self.host.get().strip(), port)
                self.error_shown = ''
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Penerima UDP', str(error))

    def toggle_recording(self):
        try:
            if self.receiver.recording:
                self.receiver.stop_recording()
            else:
                self.receiver.start_recording(self.output.get(), self.subject.get(), self.label.get(), self.notes.get())
        except (OSError, ValueError) as error:
            messagebox.showerror('Rekaman', str(error))

    def toggle_analysis(self):
        try:
            if self.cnn.snapshot()['active']:
                self.cnn.stop()
            else:
                if self.receiver.sensor_count != 8:
                    raise ValueError('CNN membutuhkan profil 8 FSR per kaki. Profil 4 tetap bisa direkam.')
                self.cnn.start()
        except (ValueError, RuntimeError) as error:
            messagebox.showerror('Analisis CNN', str(error))

    def open_results(self):
        folder = self.receiver.last_path
        if folder and Path(folder).is_dir():
            try:
                os.startfile(folder)
            except OSError as error:
                messagebox.showerror('Folder hasil', str(error))
        else:
            messagebox.showinfo('Folder hasil', 'Belum ada sesi yang direkam.')

    def refresh(self):
        state = self.receiver.snapshot()
        stats = state['stats']
        self.connection.set(('UDP aktif' if state['running'] else 'UDP berhenti') +
                            f" | valid {stats['valid']} | invalid {stats['invalid']} | ditolak {stats['rejected']}"
                            f" | celah seq {stats['gaps']} | duplikat {stats['duplicates']} | terlambat {stats['out_of_order']} | restart {stats['restarts']}")
        self.listen.configure(text='Hentikan penerima' if self.receiver.sock else 'Mulai menerima')
        for entry in (self.host_entry, self.port_entry):
            entry.configure(state='disabled' if self.receiver.sock else 'normal')
        self.profile_entry.configure(state='disabled' if self.receiver.sock else 'readonly')
        cnn = self.cnn.snapshot()
        self.analyze.configure(text='Hentikan analisis CNN' if cnn['active'] else 'Mulai analisis CNN',
                               state='normal' if state['running'] else 'disabled')
        prediction = cnn['prediction']
        if prediction:
            self.cnn_status.set(f"MODEL UJI | {prediction['label']} | Confidence {prediction['confidence']:.1%} | "
                                f"P(abnormal) {prediction['abnormal_probability']:.1%}\n"
                                f"{prediction['averaged_windows']}/10 jendela | Inference {prediction['inference_ms']:.2f} ms")
        else:
            self.cnn_status.set(cnn['message'])
        for foot, (status, sync, tree) in self.foot_ui.items():
            dev = state['devices'].get(foot)
            if not dev:
                self.imu_ui[foot].set('IMU: menunggu data')
                status.set('Menunggu ESP…')
                sync.set('Clock belum terukur')
                for i in range(4):
                    tree.item(str(i), values=(f'P{i+1}', '—', f'P{i+5}', '—'))
                continue
            live = state['running'] and dev['age_s'] < 3
            status.set(f"{'AKTIF' if live else 'TIDAK ADA DATA'} · {dev['device_id']}\n"
                       f"{dev['address'][0]}:{dev['address'][1]} · {dev['rate']} paket/s · target {dev['latest']['fs_hz']} Hz\n"
                       f"{dev['rows']} paket · celah seq {dev['gaps']} · terakhir {dev['age_s']:.1f} s")
            clock = dev['clock']
            sync.set(f"Offset {clock['offset_us']/1000:.2f} ms · RTT {clock['rtt_us']/1000:.2f} ms (estimasi)"
                     if clock else 'Belum sync / sync kedaluwarsa — raw tetap direkam')
            pressure = dev['latest']['pressure']
            mask = dev['latest'].get('sensor_valid_mask', 255)
            imu = dev['latest'].get('imu')
            self.imu_ui[foot].set(('IMU raw: accel ' + ', '.join(f'{v:g}' for v in imu[:3]) + '\ngyro ' + ', '.join(f'{v:g}' for v in imu[3:]))
                                 if live and imu and dev['latest'].get('imu_valid', True) else 'IMU: tidak ada data valid')
            for i in range(4):
                second = f'{pressure[i+4]:.2f}' if live and i+4 < len(pressure) and mask & (1 << (i+4)) else 'I2C invalid' if live else 'Tidak tersedia'
                first = f'{pressure[i]:.2f}' if live and mask & (1 << i) else 'I2C invalid' if live else 'Tidak tersedia'
                tree.item(str(i), values=(SENSOR_LAYOUT[i]['channel'], first, SENSOR_LAYOUT[i+4]['channel'], second))
        recording = state['recording']
        self.record.configure(text='Hentikan & simpan' if recording else 'Mulai rekam',
                              state='normal' if state['running'] else 'disabled')
        for widget in (self.subject_entry, self.label_entry, self.note_entry, self.output_entry, self.browse):
            widget.configure(state='disabled' if recording else 'normal')
        if recording:
            self.session.set(f"MEREKAM · kiri {state['counts']['left']} · kanan {state['counts']['right']}\n{state['path']}")
        elif state['path']:
            self.session.set(f"Rekaman ditutup · {state['path']}")
        if state['error']:
            self.session.set(state['error'])
            if state['error'] != self.error_shown:
                self.error_shown = state['error']
                messagebox.showerror('Akuisisi berhenti', state['error'])
        self.root.after(200, self.refresh)

    def close(self):
        try:
            if self.simulator:
                self.simulator.stop()
            self.receiver.stop()
        except (OSError, RuntimeError) as error:
            messagebox.showerror('Gagal menutup rekaman', str(error))
            return
        self.root.destroy()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Smart Insole research collector')
    parser.add_argument('--demo', action='store_true', help='Run two local simulated ESPs')
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root)
    if args.demo:
        from simulator import Simulator
        port = app.receiver.start('127.0.0.1', 0)
        app.host.set('127.0.0.1')
        app.port.set(str(port))
        app.subject.set('DEMO-S001')
        app.label.set('SIMULATED-walking')
        app.notes.set('SIMULATED DATA. Not for training or clinical interpretation.')
        root.title('DEMO — Smart Insole | Research acquisition')
        app.simulator = Simulator('127.0.0.1', port)
        app.simulator.start()
    root.mainloop()
