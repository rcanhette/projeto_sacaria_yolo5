import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.capture_point import CapturePoint

dummy_ct = {
    "id": 99,
    "name": "CT-TESTE",
    "source_path": "rtsp://0.0.0.0/nao_existe",
    "roi": None,
    "model_path": "sacaria_yolov5n.pt",
}

cfg = {
    "source_type": "file",
    "path": os.path.join(ROOT, "data", "nao_existe.mp4"),
    "roi": None,
    "model": "sacaria_yolov5n.pt",
}

cp = CapturePoint(dummy_ct, cfg)

print('[TEST] start_session...')
cp.start_session('LOTE-XYZ')
time.sleep(0.2)
print('[TEST] Thread ativa?', bool(cp.thread and cp.thread.is_alive()))

print('[TEST] stop_session...')
cp.stop_session()
time.sleep(0.2)

print('[TEST] Thread encerrada?', not bool(cp.thread and cp.thread.is_alive()))
print('[TEST] Camera é None?', cp.camera is None)
print('[TEST] Detector é None?', cp.detector is None)

print('[TEST] Reiniciando sessão para validar reuso...')
cp.start_session('LOTE-ABC')
time.sleep(0.2)
print('[TEST] Nova thread ativa?', bool(cp.thread and cp.thread.is_alive()))

print('[TEST] Finalizando...')
cp.stop_session()

