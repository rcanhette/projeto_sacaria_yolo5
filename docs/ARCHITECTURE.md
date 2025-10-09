Arquitetura, Fluxos e Diagramas

Este documento complementa o README principal com diagramas (Mermaid) e detalhamento de fluxos, para facilitar manutenção por novos desenvolvedores.

Arquitetura (alto nível)

```mermaid
graph TD
  subgraph Clients
    B[Browser/UI]
  end

  subgraph App[Flask App]
    R1[/routes/ct.py/]
    R2[/routes/logs.py/]
    S1[[services/capture_point.py]]
    S2[[services/video_source.py]]
    S3[[services/industrial_tag_detector.py]]
    DB[(services/db.py)]
  end

  subgraph External
    P[(PostgreSQL)]
    RTSP[[Câmeras RTSP]]
    FILE[[Arquivos de Vídeo]]
    YOLO[third_party/yolov5]
  end

  B -- HTTP(GET/POST), SSE, MJPEG --> R1
  B -- HTTP(GET) --> R2
  R1 -- start/stop/lista --> S1
  R1 -- vídeo (/ct/<id>/video) --> S1
  R1 -- SSE (/sse/ct/<id>) --> S1
  R2 -- consultas --> DB
  S1 -- get_frame() --> S2
  S1 -- detect_and_tag() --> S3
  S3 -- hubconf(load local) --> YOLO
  S1 -- create/finish/log --> DB
  DB --- P
  S2 -- CAP_FFMPEG/BUFFERSIZE --> RTSP
  S2 -- delay/FPS --> FILE
```

Pipeline de processamento

```mermaid
flowchart LR
  A[Frame capturado] --> B[Filtro ROI]
  B --> C[YOLOv5 detect]
  C --> D[Filtra classe/confiança]
  D --> E[Associa/atualiza objetos]
  E --> F[Máquina de estados<br/>(duplo cruzamento)]
  F --> G[Atualiza contador]
  G --> H[Log de deltas (DB)]
  E --> I[Desenho BBox/linhas/labels]
  I --> J[Frame para /video]
```

Sequência — Start de sessão

```mermaid
sequenceDiagram
  actor U as Usuário
  participant W as Waitress/Flask
  participant CT as CapturePoint
  participant DB as DB (session_repository)

  U->>W: POST /ct/{id}/start (lote, fonte)
  W->>W: valida permissões
  W->>W: checa sessão ativa (app + DB)
  alt já existe
    W-->>U: 200/204 Sessão já ativa
  else criar
    W->>CT: set_source(tipo, path)
    W->>CT: start_session(lote) [lock]
    CT->>DB: create_session (idempotente)
    CT->>CT: _ensure_thread() (inicia loop)
    W-->>U: 200/204 OK
  end
```

Sequência — Stop de sessão

```mermaid
sequenceDiagram
  actor U as Usuário
  participant W as Waitress/Flask
  participant CT as CapturePoint
  participant DB as DB

  U->>W: POST /ct/{id}/stop
  W->>CT: stop_session()
  CT->>DB: finish_session()
  CT->>CT: encerra thread loop
  CT->>CT: VideoSource.release() (ordem segura)
  W-->>U: 200/204 OK
```

Modelo de dados (ER simplificado)

```mermaid
erDiagram
  USERS ||--o{ USER_CT : GRANTS
  CT ||--o{ USER_CT : RELATION
  CT ||--o{ SESSION : HAS
  SESSION ||--o{ SESSION_LOG : HAS

  USERS {
    int id PK
    text username
    text password
    text role
  }

  CT {
    int id PK
    text name
    text source_path
    text roi
    text model_path
  }

  SESSION {
    int id PK
    int ct_id FK
    text lote
    timestamp data_inicio
    timestamp data_fim
    text status
    int total_final
  }

  SESSION_LOG {
    int id PK
    int session_id FK
    int ct_id FK
    timestamp ts
    int delta
    int total_atual
  }
```

Pontos de manutenção importantes
- Sessão única por CT: protegido por aplicação (lock/idempotência) e por índice único parcial no DB.
- Encerramento limpo: `services/capture_point.py.stop_session()` + `services/video_source.py.release()`.
- Tuning de performance: Waitress (`--threads`, `--connection-limit`), SSE interval, CAP_PROP_BUFFERSIZE.
- Logs/observabilidade: redirecionar stdout/stderr; ver `logs/service.out|err` quando rodar como serviço.

Runbook resumido
- Iniciar (console): `waitress-serve --host 0.0.0.0 --port 80 --threads 64 --connection-limit 500 --channel-timeout 300 --call app:create_app`
- Iniciar (serviço): `python windows_service.py install|start` (ajuste `windows_service.ini` se necessário)
- Backup: `pg_dump -h <host> -U <user> -d contagem_sacaria -F c -f backup.dump`
- Restore: `pg_restore -h <host> -U <user> -d contagem_sacaria -c backup.dump`

