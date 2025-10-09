Projeto Sacaria YOLOv5 — Documentação

Resumo
- Contagem de sacarias usando YOLOv5 com rastreamento simples e dupla linha de cruzamento.
- Frontend web em Flask com painéis por CT, streaming de vídeo/processado e SSE.
- Uso local do repositório YOLOv5 em `third_party/yolov5` (sem downloads na inicialização).

Requisitos
- Python 3.13 (recomendado). 
- PostgreSQL acessível (padrão: host `localhost`, database `contagem_sacaria`).
- Windows (suportado via serviço com pywin32) ou execução em console com Waitress.
- Pesos do modelo YOLOv5: arquivo `.pt` no diretório do projeto (padrão `sacaria_yolov5n.pt`).

Instalação
1) Clone o repositório e crie o venv:
   - `python -m venv venv`
   - `venv\Scripts\pip install --upgrade pip`
   - `venv\Scripts\pip install -r requirements.txt`
   - Observação: Torch pode exigir instalação específica. Se necessário, siga https://pytorch.org/get-started/locally e depois rode `pip install -r requirements.txt` novamente.

Configuração
- Banco de dados (variáveis de ambiente): `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`.
- YOLOv5 local e Ultralytics: defina para evitar auto-instalações
  - `YOLOV5_NO_AUTOINSTALL=1`
  - `ULTRALYTICS_NO_AUTOINSTALL=1`
- Vídeo (opcional):
  - `VIDEO_RTSP_BUFFER_SIZE` (int, default 3) — buffer do backend RTSP.
  - `VIDEO_FILE_DELAY_MS` (ms) ou `VIDEO_FILE_DELAY_FACTOR` (default 0.9) — delay para arquivos.

Execução (desenvolvimento)
- `venv\Scripts\python app.py`
- O app subirá em `http://0.0.0.0:8080` por padrão.

Execução (produção, recomendado)
- Waitress (com mais threads por causa de SSE/vídeo):
  - `venv\Scripts\waitress-serve --host 0.0.0.0 --port 80 --threads 64 --connection-limit 500 --channel-timeout 300 --call app:create_app`
- Serviço Windows (pywin32):
  - Arquivo: `windows_service.py`. Configuração opcional: `windows_service.ini`.
  - Instalar: `venv\Scripts\python windows_service.py install`
  - Iniciar: `venv\Scripts\python windows_service.py start`
  - Parar/Remover: `... stop` / `... remove`
  - `windows_service.ini` (se presente) permite ajustar `[server] host/port`, `[paths] waitress_exe/python_exe/logs_dir`, `[env]` variáveis.

Arquitetura (arquivos principais)
- `app.py` (app.py:1): fábrica Flask `create_app()`; aplica `ensure_schema()` no boot; registra blueprints.
- `routes/ct.py` (routes/ct.py:1): rotas por CT; START/STOP com validações de sessão única; SSE; streaming de vídeo.
- `routes/logs.py` (routes/logs.py:1): listagem/consulta/exportação de logs e sessões.
- `routes/auth.py`, `routes/user_admin.py`, `routes/ct_admin.py`: autenticação e administração.
- `services/db.py` (services/db.py:1): conexão PostgreSQL e migrações leves via `ensure_schema()`.
- `services/session_repository.py`: criação/log/finalização de sessão (idempotente para evitar duplicatas).
- `services/video_source.py`: captura RTSP/arquivo com thread interna e controles de buffer/delay.
- `services/industrial_tag_detector.py`: carga do YOLOv5 local e inferência (usa `third_party/yolov5`).
- `services/capture_point.py`: orquestra câmera + detector, thread de processamento, contagem, e estado da sessão.

Fluxo de dados (alto nível)
1) Fonte de vídeo (`VideoSource`) lê frames continuamente (thread interna); aplica delay/RTSP buffer.
2) `CapturePoint` executa loop de processamento: detecção (`IndustrialTagDetector`), rastreio simples e contagem por dupla linha, e envia frames para `/ct/<id>/video`.
3) Sessão: `start_session()` inicia sessão (DB + memória); `stop_session()` finaliza (DB, libera threads e conexões).
4) SSE (`/sse/ct/<id>`) envia atualizações de contagem/estado.

Prevenção de sessões duplicadas
- Em memória: lock em `CapturePoint.start_session()` e checagem de flags.
- Endpoint: `/ct/<id>/start` verifica sessão ativa no app e no DB antes de criar.
- Repositório: `create_session()` é idempotente — retorna a ativa existente.
- Banco: índice único parcial garante 1 sessão `ativo` por CT. Criado por `ensure_schema()`.

YOLOv5 local
- Código do YOLOv5 está em `third_party/yolov5` (precisa conter `hubconf.py`).
- Carregamento do modelo: `services/industrial_tag_detector.py` usa `torch.hub.load(yolo_dir, 'custom', path=...)` com `source='local'`.
- Pesos: arquivo `.pt` na raiz do projeto (padrão `sacaria_yolov5n.pt`). Configure por CT no banco/UI.

Banco de dados
- Tabelas principais: `users`, `ct`, `user_ct`, `session`, `session_log`.
- `ensure_schema()` cria/ajusta schema e índices. Também:
  - Consolida duplicatas antigas de sessão ativa (mantém a mais recente; demais viram `cancelado`).
  - Cria índice único parcial `uq_session_one_active_per_ct` em `session(ct_id) WHERE status='ativo'`.

Variáveis de ambiente úteis
- `YOLOV5_NO_AUTOINSTALL=1`, `ULTRALYTICS_NO_AUTOINSTALL=1` — desliga auto-instalações do Ultralytics.
- `VIDEO_RTSP_BUFFER_SIZE`, `VIDEO_FILE_DELAY_MS`, `VIDEO_FILE_DELAY_FACTOR` — tuning de captura.
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` — banco.

Comandos comuns (Windows)
- Ativar venv: `venv\Scripts\activate`
- Rodar dev: `python app.py`
- Rodar produção: `waitress-serve --host 0.0.0.0 --port 80 --threads 64 --connection-limit 500 --channel-timeout 300 --call app:create_app`
- Serviço: `python windows_service.py install|start|stop|remove`

Solução de problemas
- Aviso "git não é reconhecido": inofensivo (metadados); instale Git ou ignore.
- `ModuleNotFoundError` (pandas/seaborn/ultralytics): rode `pip install -r requirements.txt` no venv correto.
- Fila do Waitress (Task queue depth): aumente `--threads` e `--connection-limit`, reduza frequência do SSE.
- Exceção `cv2.error` em `cap.read()`: o código trata e encerra a thread; verifique rede RTSP/arquivo.
- Console “pausando” saída: desabilite QuickEdit no console ou rode com logs redirecionados/como serviço.

Estrutura do projeto (principais pastas)
- `routes/` — rotas Flask (ct, logs, auth, admin).
- `services/` — lógica de domínio (db, captura, detector, sessões, runtime).
- `templates/` — templates Jinja2 (UI).
- `third_party/yolov5/` — código do YOLOv5 local.
- `scripts/` — utilitários de teste (não necessários em produção).

Segurança e papéis
- Papéis: admin, supervisor, operator, viewer.
- `routes/auth.py` implementa login, guarda sessão e decorators de autorização.
- A UI e as rotas verificam permissões para iniciar/parar e visualizar CTs.

Notas finais
- Mantenha o arquivo de pesos compatível com a configuração do modelo.
- Para ajustar ROI/modelo por CT, use o módulo de administração (`ct_admin`).
- Em produção Windows, prefira serviço (pywin32) ou NSSM para evitar dependência do console.

