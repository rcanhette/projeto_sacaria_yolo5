"""
Gera um checkpoint "limpo" (apenas state_dict) a partir de um .pt do YOLOv5.

Uso (CMD/PowerShell, com venv qualquer que tenha Torch instalado):
  python scripts/sanitize_weights.py --src "C:\\caminho\\modelo.pt" --dst "C:\\caminho\\modelo_clean.pt"

Se falhar com "pathlib._local" no ambiente de conversão, instale apenas nesse venv:
  pip install pathlib
e execute novamente o script.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import ModuleType
from importlib.machinery import ModuleSpec
import torch

# Compatibilidade com checkpoints salvos em ambientes que usavam o backport 'pathlib'.
# Alguns pickles referenciam 'pathlib._local', que não existe na stdlib. Mapeamos
# esse nome para o módulo stdlib 'pathlib' para permitir o unpickle sem instalar nada.
try:
    import pathlib as _pl
    sys.modules.setdefault("pathlib._local", _pl)
except Exception:
    pass


def _try_import_yolov5_models(yolo_dir: str | os.PathLike | None):
    try:
        if yolo_dir and os.path.isdir(str(yolo_dir)):
            yolo_dir_str = str(yolo_dir)
            if yolo_dir_str not in sys.path:
                sys.path.insert(0, yolo_dir_str)
        from models.yolo import DetectionModel  # type: ignore
        return DetectionModel
    except Exception:
        return None


def _register_detectionmodel_safe(yolo_dir: str | os.PathLike | None) -> None:
    """Registra um tipo seguro para 'models.yolo.DetectionModel' sem
    depender do import real (evita puxar pandas/cv2).

    - Tenta importar a classe real e registrar.
    - Se falhar, cria uma classe dummy com __module__='models.yolo' e registra.
    """
    try:
        from torch.serialization import add_safe_globals  # type: ignore
    except Exception:
        return

    det = _try_import_yolov5_models(yolo_dir)
    if det is None:
        # Evita importar toda a arvore do YOLOv5: usa uma classe dummy compatível
        det = type("DetectionModel", (object,), {})
        try:
            det.__module__ = "models.yolo"  # informa caminho esperado pelo pickle
        except Exception:
            pass
    try:
        add_safe_globals([det])
    except Exception:
        pass


def _load_state_dict_safely(src_path: str, yolo_dir: str | os.PathLike | None) -> dict:
    # 1) Tenta modo seguro (evita unpickle de classes)
    try:
        obj = torch.load(src_path, map_location="cpu", weights_only=True)
        if isinstance(obj, dict) and all(isinstance(k, str) for k in obj.keys()):
            return {"model": obj, "names": None}
    except TypeError:
        # Versões antigas de Torch
        pass
    except Exception:
        pass

    # 2) Permite classe do YOLOv5 (real ou dummy) e tenta novamente com weights_only=True
    _register_detectionmodel_safe(yolo_dir)
    try:
        obj = torch.load(src_path, map_location="cpu", weights_only=True)
        if isinstance(obj, dict) and all(isinstance(k, str) for k in obj.keys()):
            return {"model": obj, "names": None}
    except Exception:
        pass

    # 3) Fallback (weights_only=False) e extração do state_dict do modelo
    #    - Para evitar dependências pesadas (cv2/pandas/requests/ultralytics),
    #      registramos módulos 'dummy' antes do import transitivo do YOLOv5.
    def _ensure_module(name: str) -> ModuleType:
        mod = sys.modules.get(name)
        if mod is None:
            mod = ModuleType(name)
            try:
                mod.__spec__ = ModuleSpec(name, loader=None)  # evita erros em find_spec
            except Exception:
                pass
            sys.modules[name] = mod
        return mod

    # Stubs mínimos para evitar imports pesados durante o unpickle
    for basic in ["cv2", "pandas", "requests", "ultralytics"]:
        _ensure_module(basic)

    # Estrutura de submódulos esperada pelo YOLOv5 em alguns imports
    utils_mod = _ensure_module("ultralytics.utils")
    plotting_mod = _ensure_module("ultralytics.utils.plotting")
    # Preenche alguns nomes esperados
    if not hasattr(plotting_mod, "Annotator"):
        class _Annotator:
            def __init__(self, *a, **k):
                pass
        plotting_mod.Annotator = _Annotator  # type: ignore
    if not hasattr(plotting_mod, "colors"):
        plotting_mod.colors = lambda i: (0, 255, 0)  # type: ignore
    if not hasattr(plotting_mod, "save_one_box"):
        def _save_one_box(*a, **k):
            return None
        plotting_mod.save_one_box = _save_one_box  # type: ignore

    # tqdm: se não existir, cria stub; se existir, mantém
    try:
        import tqdm as _tqdm_mod  # type: ignore
    except Exception:
        _tqdm_mod = _ensure_module("tqdm")
    if not hasattr(_tqdm_mod, "tqdm"):
        def _tqdm(x, *a, **k):
            return x
        setattr(_tqdm_mod, "tqdm", _tqdm)

    # Torchvision: só cria stubs se o import real falhar
    need_tv_stub = False
    try:
        import torchvision.transforms.functional as _tv_tf  # type: ignore
    except Exception:
        need_tv_stub = True
    if need_tv_stub:
        tv = _ensure_module("torchvision")
        tv_ops = _ensure_module("torchvision.ops")
        tv_models = _ensure_module("torchvision.models")
        tv_transforms = _ensure_module("torchvision.transforms")
    try:
        ckpt = torch.load(src_path, map_location="cpu", weights_only=False)
    except Exception as e:
        msg = str(e)
        if "pathlib._local" in msg or "'pathlib' is not a package" in msg:
            raise RuntimeError(
                "Este .pt foi salvo com backport 'pathlib'. Instale no venv de conversão: 'pip install pathlib' e repita."
            ) from e
        raise

    model = ckpt.get("ema") or ckpt.get("model")
    if model is None:
        raise RuntimeError("Checkpoint sem chaves 'model' ou 'ema'.")

    try:
        if hasattr(model, "float"):
            model = model.float()
    except Exception:
        pass

    if not hasattr(model, "state_dict"):
        raise RuntimeError("Objeto 'model' sem state_dict().")

    sd = model.state_dict()
    try:
        names = ckpt.get("names", getattr(model, "names", None))
    except Exception:
        names = None

    return {"model": sd, "names": names}


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    default_yolo_dir = repo_root / "third_party" / "yolov5"

    ap = argparse.ArgumentParser(description="Gera .pt 'limpo' (apenas state_dict) a partir de um .pt do YOLOv5.")
    ap.add_argument("--src", required=True, help="Caminho do .pt original")
    ap.add_argument("--dst", required=True, help="Caminho do .pt limpo a gerar")
    ap.add_argument(
        "--yolo_dir",
        default=str(default_yolo_dir),
        help="Pasta do YOLOv5 local (com hubconf.py, models/, utils/). Padrão: third_party/yolov5 do repositório.",
    )
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    dst = os.path.abspath(args.dst)
    yolo_dir = args.yolo_dir

    if not os.path.isfile(src):
        raise FileNotFoundError(f"Origem não encontrada: {src}")

    os.makedirs(os.path.dirname(dst), exist_ok=True)

    obj = _load_state_dict_safely(src, yolo_dir)
    torch.save(obj, dst)
    print("Gerado:", dst)


if __name__ == "__main__":
    main()
