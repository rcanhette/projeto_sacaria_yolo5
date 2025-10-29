"""
Reempacota um state_dict do YOLOv5 (arquivo "limpo" gerado pela sanitização)
em um checkpoint completo com o objeto de modelo, para que o carregamento via
torch.hub (hubconf) funcione sem erro 'OrderedDict' has no attribute 'to'.

Uso:
  python scripts/repackage_yolov5_state_dict.py \
    --src "C:\\...\\best_clean.pt" \
    --dst "C:\\...\\best_runtime.pt" \
    --yolo_dir "third_party/yolov5" \
    --yaml "yolov5n.yaml"

Observações:
- --yaml aceita apenas o nome do arquivo dentro de models/ (ex.: yolov5n.yaml)
  ou um caminho completo para o YAML do modelo.
- O script reaproveita os 'names' do arquivo de entrada quando existirem.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict

import torch
import yaml


def _alias_pathlib_local() -> None:
    try:
        import pathlib as _pl
        sys.modules.setdefault("pathlib._local", _pl)
    except Exception:
        pass


def _resolve_yaml_path(yolo_dir: str, yaml_arg: str) -> str:
    # Se for caminho absoluto/relativo existente, retorna direto
    p = Path(yaml_arg)
    if p.exists():
        return str(p)
    # Tenta em <yolo_dir>/models/<yaml_arg>
    cand = Path(yolo_dir) / "models" / yaml_arg
    if cand.exists():
        return str(cand)
    raise FileNotFoundError(f"YAML do modelo nao encontrado: {yaml_arg}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Reempacota state_dict em checkpoint completo do YOLOv5.")
    ap.add_argument("--src", required=True, help=".pt de entrada (limpo/state_dict)")
    ap.add_argument("--dst", required=True, help=".pt de saida (com objeto de modelo)")
    ap.add_argument("--yolo_dir", default=str(Path(__file__).resolve().parents[1] / "third_party" / "yolov5"),
                    help="Diretorio local do YOLOv5 (com hubconf.py, models/ etc)")
    ap.add_argument("--yaml", default="yolov5n.yaml", help="YAML do modelo (nome em models/ ou caminho absoluto)")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    dst = os.path.abspath(args.dst)
    yolo_dir = os.path.abspath(args.yolo_dir)
    yaml_path = _resolve_yaml_path(yolo_dir, args.yaml)

    if not os.path.isfile(src):
        raise FileNotFoundError(f"Arquivo de entrada nao encontrado: {src}")
    if not os.path.isdir(yolo_dir):
        raise FileNotFoundError(f"Diretorio YOLOv5 nao encontrado: {yolo_dir}")

    os.makedirs(os.path.dirname(dst), exist_ok=True)

    _alias_pathlib_local()

    # Carrega o checkpoint limpo
    ck = torch.load(src, map_location="cpu")
    if isinstance(ck, dict) and "model" in ck and isinstance(ck["model"], dict):
        state_dict: Dict[str, Any] = ck["model"]  # state_dict puro
        names = ck.get("names")
    elif isinstance(ck, dict) and all(isinstance(k, str) for k in ck.keys()):
        # arquivo pode ser diretamente o state_dict
        state_dict = ck  # type: ignore
        names = None
    else:
        raise RuntimeError("Entrada nao parece um state_dict limpo. Use o .pt sanitizado ou um dict puro.")

    # Ajusta sys.path e importa o YOLOv5 local
    if yolo_dir not in sys.path:
        sys.path.insert(0, yolo_dir)
    try:
        from models.yolo import Model  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Falha ao importar Model do YOLOv5 em {yolo_dir}: {e}")

    # Prepara nomes/classes
    if names is None:
        # fallback basico: 1 classe generica
        names = {0: "object"}
    if isinstance(names, list):
        names = dict(enumerate(names))
    if not isinstance(names, dict):
        raise RuntimeError("Campo 'names' invalido. Deve ser lista ou dict {idx: nome}.")
    nc = len(names)

    # Construi o modelo e carrega pesos
    try:
        model = Model(yaml_path, ch=3, nc=nc)  # type: ignore
    except TypeError:
        # Assinaturas diferentes em forks; tenta sem nc
        model = Model(yaml_path)  # type: ignore
        # Tenta ajustar atributo de classes se existir
        try:
            if hasattr(model, "nc"):
                setattr(model, "nc", nc)
        except Exception:
            pass

    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    if missing:
        print(f"[aviso] Parametros ausentes no state_dict: {len(missing)}")
    if unexpected:
        print(f"[aviso] Parametros inesperados no state_dict: {len(unexpected)}")

    # Atribui nomes e coloca em eval()
    try:
        model.names = names  # type: ignore[attr-defined]
    except Exception:
        pass
    model.eval()

    # Salva checkpoint com objeto de modelo
    out = {"model": model, "names": names}
    torch.save(out, dst)
    print("Gerado:", dst)


if __name__ == "__main__":
    main()

