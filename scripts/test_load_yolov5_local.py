import os
import sys

# Garante raiz do projeto no sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('YOLOV5_NO_AUTOINSTALL', '1')

print('[TEST] Importando IndustrialTagDetector...')
from services.industrial_tag_detector import IndustrialTagDetector

print('[TEST] Instanciando detector (carregamento local do YOLOv5)...')
detector = IndustrialTagDetector()

print('[TEST] Modelo carregado?:', detector.model is not None)
print('[TEST] Device:', getattr(detector, 'device', 'n/a'))

print('[TEST] Execução concluída.')

