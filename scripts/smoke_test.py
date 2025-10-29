import os
import sys
from pathlib import Path


def main() -> int:
    print("[SMOKE] Python:", sys.version)
    print("[SMOKE] Executable:", sys.executable)

    # Core deps
    try:
        import torch  # type: ignore
        print("[SMOKE] torch:", torch.__version__)
    except Exception as e:
        print("[SMOKE][WARN] torch import failed:", e)

    try:
        import cv2  # type: ignore
        print("[SMOKE] opencv:", cv2.__version__)
    except Exception as e:
        print("[SMOKE][WARN] opencv import failed:", e)

    try:
        from importlib.metadata import version
        print("[SMOKE] ultralytics:", version("ultralytics"))
    except Exception as e:
        print("[SMOKE][WARN] ultralytics not found:", e)

    root = Path(__file__).resolve().parents[1]
    yolo_dir = root / "third_party" / "yolov5"
    print("[SMOKE] YOLOv5 dir:", yolo_dir)
    if not (yolo_dir / "hubconf.py").is_file():
        print("[SMOKE][ERROR] hubconf.py não encontrado em:", yolo_dir)
        return 1

    # Teste de carregamento local leve (opcional)
    # Respeita YOLOV5_NO_AUTOINSTALL para evitar pip automático
    os.environ.setdefault("YOLOV5_NO_AUTOINSTALL", "1")
    weights = None
    for candidate in (root / "best.pt", root / "sacaria_yolov5n.pt"):
        if candidate.is_file():
            weights = candidate
            break
    if weights is None:
        print("[SMOKE] Nenhum .pt encontrado (best.pt ou sacaria_yolov5n.pt). Pulando load.")
        return 0

    try:
        import torch  # type: ignore
        print("[SMOKE] Tentando torch.hub.load local (pode demorar alguns segundos)...")
        m = torch.hub.load(str(yolo_dir), 'custom', path=str(weights), source='local', force_reload=False)
        ok = hasattr(m, 'eval')
        print("[SMOKE] Modelo carregado?", ok)
        return 0
    except Exception as e:
        print("[SMOKE][WARN] Falha ao carregar modelo local:", e)
        print("[SMOKE] Dica: use scripts/sanitize_weights.py para gerar um _clean.pt se houver erro pathlib._local.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

