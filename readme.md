# Projeto Sacaria YOLOv5

## Visao geral

Aplicacao Flask para contagem de sacarias utilizando um modelo YOLOv5 executado localmente. O sistema oferece um painel web para monitorar as TCs (cameras), iniciar/parar contagens, exportar logs e registrar imagens das sacarias identificadas que nao foram contabilizadas.

## Requisitos principais

- Windows 10/11 ou Windows Server (testado com Python 3.13).
- Python 3.10+ com `pip`.
- PostgreSQL acessivel (padrao: `localhost`, database `contagem_sacaria`, usuario `postgres`).
- Camera RTSP ou arquivos de video conforme configuracao das TCs.

As dependencias Python estao listadas em [`requirements.txt`](requirements.txt).

### Pacotes separados: Central x Agente

Para facilitar a implantação, este repositório agora inclui conjuntos separados de arquivos e dependências:

- Central (servidor web + banco): leve, sem bibliotecas de visão.
- Agente (captura + detecção): inclui OpenCV, PyTorch e pesos YOLO.

Arquivos de requisitos dedicados:
- `requirements-central.txt` – apenas Flask/Waitress/PostgreSQL/OpenPyXL.
- `requirements-agent.txt` – visão computacional, PyTorch, Ultralytics, etc.

Gerar os pacotes prontos (Windows/PowerShell):

```
powershell -ExecutionPolicy Bypass -File scripts\build_packages.ps1 -OutDir dist
```

Isso cria `dist\central\` e `dist\agent\` com os arquivos necessários para cada papel.

Notas:
- O Central pode operar sem OpenCV quando todas as TCs usam agente remoto (import de `cv2` agora é sob demanda em `routes/tc.py`).
- O Agente expõe um pequeno servidor Flask (pode rodar com Waitress) e depende de OpenCV/PyTorch.

## Instalacao rapida (NSSM)

A partir desta versão, a instalação é separada em dois pacotes: Central e Agente.

Prepare os pacotes (opcional, recomendado):

```
powershell -ExecutionPolicy Bypass -File scripts\build_packages.ps1 -OutDir dist
```

Você terá `dist\central\` e `dist\agent\` prontos para copiar para cada máquina.

Central (Servidor)
1. No servidor, copie a pasta `dist\central` para `C:\projeto_sacaria_central`.
2. PowerShell como Administrador em `C:\projeto_sacaria_central`:
   ```cmd
   python -m venv venv
   .\venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install -r requirements-central.txt
   ```
3. Crie/edite `central.ini` (UTF-8):
   - `[database]`: `host`, `port`, `database`, `user`, `password`
   - `[env]`: garanta ao menos
     - `PYTHONIOENCODING=utf-8`
     - `PYTHONUNBUFFERED=1`
     - `PGPASSFILE=NUL`
     - `PGSERVICE=` e `PGSERVICEFILE=` (vazios)
   - `[paths]`: `waitress_exe=C:\projeto_sacaria_central\venv\Scripts\waitress-serve.exe`, `logs_dir=logs`
   - `[server]`: `host=0.0.0.0`, `port=80` (ou `8080`), `threads=16` etc.
4. NSSM (GUI: `nssm edit ProjetoSacaria_v1` ou via comandos):
   - `Application`: `C:\projeto_sacaria\central\venv\Scripts\waitress-serve.exe`
   - `Startup directory`: `C:\projeto_sacaria\central`
   - `Arguments`: `--host 0.0.0.0 --port 80 --threads 16 --backlog 2048 --connection-limit 200 --channel-timeout 90 --call app:create_app`
   - Aba I/O: `AppStdout=logs\service.out.log`, `AppStderr=logs\service.err.log`, rotação ativada
   - Aba Environment (opcional se já no ini): `PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1`, `PGPASSFILE=NUL`
5. Inicie: `nssm start ProjetoSacaria_v1`. Se aparecer `PAUSED`, verifique porta/permissão e os logs em `logs\service.err.log`.

Instalação automatizada (Central):

```
powershell -ExecutionPolicy Bypass -File scripts\install_nssm_central.ps1 -ServiceName ProjetoSacaria_v1 -Root C:\projeto_sacaria_central -Port 80
```

Agente (PC/TC)
1. No PC do ponto, copie `dist\agent` para `C:\projeto_sacaria_agent`.
2. PowerShell como Administrador em `C:\projeto_sacaria_agent`:
   ```cmd
   python -m venv venv
   .\venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install -r requirements-agent.txt
   ```
3. Crie/edite `agent.ini` (UTF-8):
   - `[agent]`: `id`, `tc_id`, `central_url` (ex.: `http://SEU_SERVIDOR:80`), `token` (se aplicável)
   - HTTPS e certificado self-signed:
     - `central_verify=false` para ignorar validação (ambiente controlado)
     - ou `central_ca=C:\caminho\seu_certificado.pem` para validar com seu CA
4. Executar manualmente para teste:
   ```cmd
   python agent_app.py
   # ou
   waitress-serve --host 0.0.0.0 --port 9090 --call agent_app:create_agent_app
   ```
5. Instalar como serviço (NSSM):
   - `Application`: `C:\projeto_sacaria_agent\venv\Scripts\python.exe` (se quiser rodar o script) ou `...\waitress-serve.exe`
   - `Startup directory`: `C:\projeto_sacaria_agent`
   - Se usar Python: `Arguments`: `agent_app.py`
   - Se usar Waitress: `Arguments`: `--host 0.0.0.0 --port 9090 --call agent_app:create_agent_app`
   - I/O: `logs\agent.out.log`, `logs\agent.err.log`

Instalação automatizada (Agente):

```
powershell -ExecutionPolicy Bypass -File scripts\install_nssm_agent.ps1 -ServiceName ProjetoSacaria_Agent -Root C:\projeto_sacaria_agent -Mode waitress -Port 9090
```

## Preparacao do ambiente (desenvolvimento/homologacao)

1. Clone o repositorio para `C:\workspace\python\projeto_sacaria_yolo5` (ou outro diretorio).
2. Crie um ambiente virtual (opcional, mas recomendado):
   ```cmd
   python -m venv venv
   venv\Scripts\activate
   ```
3. Instale as dependencias:
   ```cmd
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. Configure o PostgreSQL conforme necessario. As variaveis `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` e `PGPASSWORD` podem ser definidas no ambiente ou na secao `[database]` do arquivo `windows_service.ini`.
5. Ajuste as TCs (camera, offsets, fluxo, pasta de snapshots) acessando o painel administrativo apos subir a aplicacao.

## Execucao local (modo console)

1. Garanta que o banco esta ativo.
2. Ative o ambiente virtual (caso tenha criado).
3. Inicie o servidor:
   ```cmd
   python app.py
   ```
4. O servidor escuta em `http://0.0.0.0:8080`. Ajuste host/porta via variaveis de ambiente `APP_HOST` e `APP_PORT` antes de executar.

### Credenciais padrao
- Ao iniciar pela primeira vez, o sistema garante o schema do banco e cria automaticamente um usuario administrador padrao.
- Usuario: `admin`
- Senha: `admin`
- Recomenda-se alterar a senha apos o primeiro acesso.

## Modo distribuido (Agente Local por TC)

Nesta topologia cada ponto de captura roda um "agente" local que processa a camera e envia eventos ao servidor central.

- Servidor Central: roda `app.py` (front + banco). Porta padrao 8080.
- Agente Local (por PC/TC): roda `agent_app.py` no PC do ponto. Porta padrao 9090.
- Comunicacao:
  - Agente → Central: heartbeat + eventos de sessao (start/update/finish) via HTTP.
  - Central → Agente: comandos start/stop via HTTP.
  - Front → Central: SSE para atualizacoes em tempo real (telas inalteradas).

Passos (Central)
1. Siga a secao "Execucao local" ou instale como servico Windows.
2. No painel admin, cadastre as TCs normalmente (nome, fonte RTSP, etc.). Anote o `id` da TC.
3. Garanta que a porta 8080 esteja acessivel pelos PCs dos pontos (firewall).

Passos (Agente em cada PC do ponto)
1. Instale Python 3.x e dependencias do projeto (o agente reutiliza o mesmo repo e `requirements.txt`).
2. Edite `windows_service.ini` (secao `[agent]`):
   - `tc_id=<ID da TC>` (ex.: 1)
   - `central_url=http://<HOST_DO_SERVIDOR>:8080`
   - `id=<identificador livre do agente>` (ex.: `pc1-ct1`) — informativo
3. Configuração de captura (URL, ROI, modelo, offsets) agora é centralizada no cadastro da TC no servidor. O agente busca essas informações automaticamente ao iniciar/receber START.
4. (Opcional) Ajuste `[database]` se for rodar central localmente no mesmo PC; para agente nao e necessario.
5. Execute o agente:
   ```cmd
   python agent_app.py
   ```
   - O agente envia heartbeat a cada 10s; o dashboard mostra "Agente Online" quando o servidor o vê ativo.
   - Para rodar como servico Windows, use o NSSM e aponte para `python agent_app.py` (similar ao central).

Portas e firewall
- Central: 8080/TCP (entrada) — acessivel a navegadores e aos agentes.
- Agente: 9090/TCP (entrada) — acessivel pelo servidor central para start/stop.

Observacoes
- Sem token: neste momento a autenticacao do agente esta desativada; a resolucao do host do agente e feita pelo ultimo heartbeat da TC.
- Se o agente estiver offline, o central pode operar localmente (quando configurado com acesso a camera), mantendo compatibilidade.

## Snapshots de sacarias nao contadas

- No cadastro da TC informe **Pasta para imagens das sacarias identificadas** (ex.: `C:\workspace\python\projeto_sacaria_yolo5\fotos` ou `\\servidor\compartilhamento`).
- Durante a execucao, sempre que uma sacaria for identificada e sair do fluxo sem ser contabilizada, o sistema salva uma imagem em `caminho_configurado\<lote>\HHMMSS_id<ID>.jpg`.
- Logs `INFO` confirmam o salvamento e logs `WARNING/ERROR` informam falhas (permissao, recorte invalido etc.).

## Instalacao como servico Windows

### Arquivo `windows_service.ini`

- `[server]` define `host`, `port` e ajustes de performance (`threads`, `backlog`, `connection_limit`, `channel_timeout`) utilizados pelo Waitress.
- `[paths]` permite especificar o Python/Waitress/NSSM e o diretorio de logs.
- `[database]` define `host`, `port`, `database`, `user`, `password` do PostgreSQL (replicados em variaveis de ambiente pelo servico).
- `[env]` aceita variaveis adicionais que devem estar presentes ao iniciar o processo.

### Instalacao via NSSM (Non-Sucking Service Manager)

1) Central (Servidor)

1. Baixe o NSSM em https://nssm.cc/download e deixe `nssm.exe` no PATH (ex.: `C:\Tools\nssm\nssm.exe`).
2. Garanta o Python/venv com dependencias (`venv\Scripts\python.exe`, `pip install -r requirements.txt`).
3. Crie `central.ini` a partir de `central.ini.example` e ajuste:
   - `[database]` (host, port, database, user, password)
   - `[server]` (host/port/threads, se desejar)
   - `[paths]` (opcional: logs_dir, python_exe, waitress_exe, nssm_exe)
4. (Opcional) Defina `CENTRAL_INI=C:\caminho\central.ini` se quiser manter o INI fora do projeto.
5. Prompt/PowerShell como administrador, na raiz do projeto:
   ```cmd
   scripts\install_windows_service_nssm.bat
   ```
   - O instalador usa `service_config.py` para ler `central.ini`, cria/atualiza o serviço com runner `scripts\nssm_service_runner.py` e grava logs em `logs\service.out/.err`.
6. Inicie/gerencie o serviço:
   ```cmd
   nssm start ProjetoSacaria_v1
   nssm stop ProjetoSacaria_v1
   nssm remove ProjetoSacaria_v1 confirm
   ```
7. Ao atualizar o código ou `central.ini`, rode novamente o instalador e reinicie o serviço.

2) Agente (PC do ponto)

1. No PC do ponto, prepare Python/venv e instale as dependencias do projeto.
2. Crie `agent.ini` a partir de `agent.ini.example` e ajuste em `[agent]`:
   - `id` (identificador livre), `tc_id` (ID da TC no Central), `central_url` (URL do servidor Central visível desse PC).
3. (Opcional) Defina `AGENT_INI=C:\caminho\agent.ini` se quiser manter o INI fora do projeto.
4. Prompt/PowerShell como administrador, na raiz do projeto do agente:
   ```cmd
   scripts\install_agent_nssm.bat
   ```
5. Inicie/gerencie o serviço do agente:
   ```cmd
   nssm start ProjetoSacaria_Agent_<id>
   nssm stop ProjetoSacaria_Agent_<id>
   nssm remove ProjetoSacaria_Agent_<id> confirm
   ```
6. Logs do serviço do agente: `logs\agent_service.out` e `logs\agent_service.err`.

### Instalacao do Agente via NSSM

1. Crie `agent.ini` a partir de `agent.ini.example` e ajuste a seção `[agent]` (id, tc_id, central_url).
2. Garanta o Python/venv do projeto (`venv\Scripts\python.exe`) instalado com `pip install -r requirements.txt`.
3. Abra um Prompt/PowerShell como administrador e execute:
   ```cmd
   scripts\install_agent_nssm.bat
   ```
4. Inicie o serviço do agente:
   ```cmd
   nssm start ProjetoSacaria_Agent_<id>
   ```
5. Logs do serviço do agente: `logs\agent_service.out` e `logs\agent_service.err`.
6. Para reinstalar/atualizar, execute novamente o instalador e reinicie o serviço.

### Checklist de instalacao em um novo servidor

## Guia Rápido (Central + Agente + YOLOv5 local)

1) Central (servidor)
- Criar venv e instalar dependências:
  ```cmd
  cd C:\workspace\python\projeto_sacaria_yolo5
  py -3.12 -m venv venv
  venv\Scripts\activate
  python -m pip install -U pip setuptools wheel
  pip install -r requirements.txt
  ```
- Configurar banco (se necessário) e iniciar o servidor de desenvolvimento:
  ```cmd
  python app.py
  ```
- (Opcional) Instalar como serviço via NSSM:
  ```cmd
  scripts\install_windows_service_nssm.bat
  nssm start ProjetoSacaria_v1
  ```

2) Agente (PC da CT) com YOLOv5 local
- Preparar estrutura do agente (exemplo de caminho): `C:\projeto_sacaria\projeto_sacaria_agent`
  - Garantir que `third_party\yolov5\hubconf.py` e pastas `models/`, `utils/` existam (cópia do YOLOv5 “vanilla”).
  - Colocar o(s) modelo(s) `.pt` (ex.: `best.pt` ou `sacaria_yolov5n.pt`) na raiz do agente.
- Criar venv e instalar dependências de CPU:
  ```cmd
  cd C:\projeto_sacaria\projeto_sacaria_agent
  py -3.12 -m venv venv
  venv\Scripts\activate
  python -m pip install -U pip setuptools wheel
  pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
  pip install --only-binary=:all: numpy opencv-python-headless pillow pyyaml requests tqdm pandas scipy ultralytics
  ```
- (Opcional) Evitar auto-instalação do YOLOv5:
  ```cmd
  set YOLOV5_NO_AUTOINSTALL=1
  ```
- (Se necessário) Sanitizar pesos `.pt` que acusem `pathlib._local`:
  ```cmd
  python scripts\sanitize_weights.py --src "C:\projeto_sacaria\projeto_sacaria_agent\best.pt" --dst "C:\projeto_sacaria\projeto_sacaria_agent\best_clean.pt" --yolo_dir "C:\projeto_sacaria\projeto_sacaria_agent\third_party\yolov5"
  ren "C:\projeto_sacaria\projeto_sacaria_agent\best.pt" "best_old.pt"
  ren "C:\projeto_sacaria\projeto_sacaria_agent\best_clean.pt" "best.pt"
  ```
- Teste rápido do ambiente do agente:
  ```cmd
  python -c "import torch,cv2,sys; from importlib.metadata import version; print('torch',torch.__version__,'opencv',cv2.__version__,'ultralytics',version('ultralytics')); print(sys.executable)"
  python scripts\smoke_test.py
  ```
- Rodar agente em desenvolvimento:
  ```cmd
  python agent_app.py
  ```
- (Opcional) Instalar agente como serviço via NSSM:
  ```cmd
  scripts\install_agent_nssm.bat
  nssm start ProjetoSacaria_Agent_<id>
  ```

3) Monitoramento e manutenção do Agente
- Health: `GET http://<host_agente>:9090/api/agent/v1/health` (pendências, severidade, sessão atual)
- Pendências: `GET http://<host_agente>:9090/api/agent/v1/pending?limit=50`
- Sync imediato: `POST http://<host_agente>:9090/api/agent/v1/sync`
- Compactação: `POST http://<host_agente>:9090/api/agent/v1/compact` (body opcional `{ "hard": true }`)

Mais detalhes:
- Guia de instalação do YOLOv5 local: `docs/LOCAL_YOLOV5_INSTALL.md`
- Fila local do agente e endpoints: `docs/AGENT_QUEUE.md`

## Documentação importante

- Instalação do YOLOv5 local no agente (CPU) e sanitização de pesos: veja `docs/LOCAL_YOLOV5_INSTALL.md`.
- Fila local durável do Agente, endpoints de saúde, sync e compactação: veja `docs/AGENT_QUEUE.md`.
- Teste rápido do ambiente do Agente: `scripts/smoke_test.py`.

1. Instalar Python 3.x (x64) e adicionar ao PATH.
2. Clonar/copiar o repositorio para o diretorio final (ex.: `C:\workspace\python\projeto_sacaria_yolo5`).
3. Criar `venv` e instalar dependencias com `pip install -r requirements.txt`.
4. Criar o banco e configurar a secao `[database]` do `windows_service.ini`.
5. Ajustar `[server]` e `[paths]` conforme o ambiente.
6. Instalar o servico via `scripts\install_windows_service_nssm.bat`.
7. Configurar em cada TC a pasta de snapshots e demais parametros.
8. Validar logs em `logs\service.out` e `logs\service.err`.
9. Abrir a porta configurada no firewall ou load balancer, se necessario.

### Instalacao do Agente como servico Windows (por ponto de captura)

Siga “Instalação via NSSM → 2) Agente (PC do ponto)”. Abra a porta 9090/TCP no firewall do PC do ponto. Valide no dashboard que o agente aparece como “Agente Online” e que o start/stop remoto funciona.
## Estrutura principal do projeto

```
app.py                    # Entrada Flask (create_app + run)
config.py                 # Configuracao inicial das TCs
services/                 # Camada de servicos (detector, banco, repositorios, runtime)
routes/                   # Blueprints Flask (tc, logs, auth, administracao)
templates/                # Templates Jinja2 (painel web)
scripts/                  # Utilitarios (instalacao do servico, testes)
service_config.py + windows_service.ini # Configuracao compartilhada do servico Windows
third_party/yolov5/       # Repositorio YOLOv5 localizado
logs/                     # Logs do servico/Waitress (configuravel)
```

## Documentacao complementar

- Arquitetura geral: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- Deploy passo a passo: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
- Dependencias: [`requirements.txt`](requirements.txt).
- Utilitarios: pasta [`scripts/`](scripts/).

Consulte os logs do servico ou as configuracoes das TCs para diagnosticar eventuais problemas de captura.
