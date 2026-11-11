"""Analyze a closed recording with the same window builder used by live inference."""
import csv
import hashlib
import heapq
import json
import math
import uuid
from pathlib import Path

from cnn import GaitModel, GaitWindows, MODEL_DIR
from sensor_contract import SAMPLING_RATE, INPUT_LENGTH


def _stream(path, foot, processed):
    with path.open(newline='', encoding='utf-8') as file:
        for row in csv.DictReader(file):
            def value(name):
                result = float(row[name])
                if not math.isfinite(result):
                    raise ValueError(f'CSV berisi nilai tidak valid: {name}')
                return result
            axes = ('ax', 'ay', 'az', 'gx', 'gy', 'gz')
            prefix = 'corrected_' if processed else ''
            imu = [value(prefix+axis) for axis in axes] if all(row.get(prefix+axis, '') for axis in axes) else None
            keys = [f'filtered{i}' if processed else f'p{i}' for i in range(1, 9)]
            if not all(row.get(key, '') for key in keys):
                if processed:
                    raise ValueError('Model membutuhkan kolom kalibrasi yang belum tersedia')
                raise ValueError('CNN membutuhkan delapan kanal per kaki')
            data = dict(foot=foot, device_id=row['device_id'], boot_id=row['boot_id'],
                        seq=int(row['seq']), sample_index=int(row['sample_index']), t_us=int(row['esp_t_us']),
                        fs_hz=int(row['fs_hz']), pressure=[value(key) for key in keys], imu=imu,
                        sensor_valid_mask=int(row.get('sensor_valid_mask') or 255),
                        imu_valid=row.get('imu_valid', 'True').lower() not in ('false', '0'))
            rx = int(row['host_rx_us'])
            clock = None
            if all(row.get(key, '') for key in ('clock_offset_us', 'clock_rtt_us', 'clock_age_us')):
                clock = dict(offset_us=value('clock_offset_us'), rtt_us=value('clock_rtt_us'),
                             measured_host_us=rx-value('clock_age_us'))
            yield rx, data, clock, row['sequence_status']


def analyze_recording(folder):
    folder = Path(folder)
    metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    if metadata.get('status') != 'complete':
        raise ValueError('Sesi belum tersimpan lengkap')
    if metadata.get('input_format') == 'public_pressure_100hz':
        from public_model import analyze
        return analyze(folder)
    model_meta = json.loads((MODEL_DIR / 'gait_test_model.json').read_text(encoding='utf-8'))
    preprocessing = model_meta.get('preprocessing', 'firmware_response')
    if preprocessing not in ('firmware_response', 'host_calibration_v1'):
        raise ValueError('Kontrak preprocessing model tidak didukung')
    processed = preprocessing == 'host_calibration_v1'
    if processed and any(not metadata.get('calibration', {}).get(kind, {}).get(foot)
                         for kind in ('pressure', 'imu') for foot in ('left', 'right')):
        raise ValueError('Model ini memerlukan kalibrasi tekanan dan IMU kedua kaki')
    output = folder / 'analyses' / uuid.uuid4().hex[:12]
    output.mkdir(parents=True, exist_ok=False)
    builder = GaitWindows()
    model = None
    timeline, rows, rejects = [], 0, 0
    try:
        with (output / 'predictions.csv').open('x', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=['start_s', 'end_s', 'label', 'abnormal_probability', 'inference_ms'])
            writer.writeheader()
            streams = [_stream(folder / f'{foot}.csv', foot, processed) for foot in ('left', 'right')]
            for rx, packet, clock, status in heapq.merge(*streams, key=lambda item: item[0]):
                rows += 1
                revision = builder.revision
                ready = builder.add(packet, rx, clock, status)
                rejects += int(builder.revision != revision)
                for end, samples in ready:
                    if model is None:
                        model = GaitModel()
                    probability, latency = model.predict(samples)
                    relative_end = (end-metadata['started_host_us']) / 1e6
                    prediction = dict(start_s=relative_end-(INPUT_LENGTH-1)/SAMPLING_RATE, end_s=relative_end,
                                      label='ABNORMAL' if probability >= model_meta['decisionThreshold'] else 'NORMAL',
                                      abnormal_probability=probability, inference_ms=latency)
                    writer.writerow(prediction)
                    timeline.append(prediction)
        average = sum(p['abnormal_probability'] for p in timeline)/len(timeline) if timeline else None
        result = dict(status='complete' if timeline else 'insufficient_data', session=folder.name,
                      subject=metadata['subject'], label=metadata['label'],
                      result=('ABNORMAL' if average >= model_meta['decisionThreshold'] else 'NORMAL') if timeline else None,
                      abnormal_probability=average,
                      confidence=(average if average >= model_meta['decisionThreshold'] else 1-average) if timeline else None,
                      windows=len(timeline), rows=rows, discontinuities=rejects,
                      recording_seconds=(metadata['ended_host_us']-metadata['started_host_us'])/1e6,
                      message='Analisis selesai' if timeline else builder.message,
                      model_version=model_meta['modelVersion'], purpose=model_meta['purpose'],
                      preprocessing=preprocessing, calibration_applied_to_model=processed,
                      weights_sha256=hashlib.sha256((MODEL_DIR / 'gait_test_model.tflite').read_bytes()).hexdigest(),
                      timeline=timeline, analysis_id=output.name)
        temporary = output / 'result.tmp'
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(output / 'result.json')
        return result
    except Exception:
        (output / 'INCOMPLETE.txt').write_text('Analisis gagal. Data akuisisi asli tetap tersimpan.', encoding='utf-8')
        raise
