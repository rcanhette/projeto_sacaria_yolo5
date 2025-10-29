# Instalação do YOLOv5 Local (Agente)

Este guia descreve como preparar o venv do Agente para executar o YOLOv5 localmente usando o repositório `third_party/yolov5` e como resolver o erro `pathlib._local` com a sanitização de pesos.

## Requisitos

- Python 64‑bits 3.11/3.12
- Estrutura do YOLOv5 em `third_party/yolov5` (com `hubconf.py`, `models/`, `utils/`, etc.)
- Modelo `.pt` (ex.: `sacaria_yolov5n.pt`, `best.pt`)

## Passos (Windows, CMD)

1) Criar e ativar venv do Agente
```
cd C:\projeto_sacaria\projeto_sacaria_agent
py -3.12 -m venv venv
venv\Scripts\activate.bat
```

2) Instalar dependências (CPU)
```
python -m pip install -U pip setuptools wheel
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install --only-binary=:all: numpy opencv-python-headless pillow pyyaml requests tqdm pandas scipy ultralytics
```

3) Evitar auto-instalação do YOLOv5 (opcional)
```
set YOLOV5_NO_AUTOINSTALL=1
```

4) Sanitizar pesos `.pt` (se ocorrer `pathlib._local`)

Alguns `.pt` foram salvos em um ambiente que incluiu um backport do `pathlib`. Para usar em ambientes “limpos”, gere um arquivo “limpo” com apenas `state_dict`:

```
python scripts\sanitize_weights.py --src "C:\projeto_sacaria\projeto_sacaria_agent\best.pt" --dst "C:\projeto_sacaria\projeto_sacaria_agent\best_clean.pt" --yolo_dir "C:\projeto_sacaria\projeto_sacaria_agent\third_party\yolov5"
ren "C:\projeto_sacaria\projeto_sacaria_agent\best.pt" "best_old.pt"
ren "C:\projeto_sacaria\projeto_sacaria_agent\best_clean.pt" "best.pt"
```

Se o sanitizador reclamar do `pathlib._local`, instale apenas para o venv de conversão:
```
pip install pathlib
```
e execute o `sanitize_weights.py` novamente.

5) Teste rápido de dependências
```
python -c "import torch,cv2,sys; from importlib.metadata import version; print('torch',torch.__version__,'opencv',cv2.__version__,'ultralytics',version('ultralytics')); print(sys.executable)"
python scripts\smoke_test.py
```

## Observações

- Prefira Python 3.11/3.12 64‑bits. Em 3.14, wheels podem não existir e a instalação pode tentar compilar (falha no Windows sem MSVC).
- “git não encontrado” é apenas aviso informativo do Ultralytics; não impede o YOLOv5 local.

