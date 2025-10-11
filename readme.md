# Projeto Sacaria YOLOv5

## Visao geral

Aplicacao Flask para contagem de sacarias utilizando um modelo YOLOv5 executado localmente. O sistema oferece um painel web para monitorar as TCs (cameras), iniciar/parar contagens, exportar logs e registrar imagens das sacarias identificadas que nao foram contabilizadas.

## Requisitos principais

- Windows 10/11 ou Windows Server (testado com Python 3.13).
- Python 3.10+ com `pip`.
- PostgreSQL acessivel (padrao: `localhost`, database `contagem_sacaria`, usuario `postgres`).
- Camera RTSP ou arquivos de video conforme configuracao das TCs.

As dependencias Python estao listadas em [`requirements.txt`](requirements.txt).

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

## Snapshots de sacarias nao contadas

- No cadastro da TC informe **Pasta para imagens das sacarias identificadas** (ex.: `C:\workspace\python\projeto_sacaria_yolo5\fotos` ou `\\servidor\compartilhamento`).
- Durante a execucao, sempre que uma sacaria for identificada e sair do fluxo sem ser contabilizada, o sistema salva uma imagem em `caminho_configurado\<lote>\HHMMSS_id<ID>.jpg`.
- Logs `INFO` confirmam o salvamento e logs `WARNING/ERROR` informam falhas (permissao, recorte invalido etc.).

## Instalacao como servico Windows

1. Edite `windows_service.ini`:
   - `[server]` define `host` e `port` utilizados pelo Waitress.
   - `[paths]` permite especificar o Python/Waitress e o diretorio de logs.
   - `[database]` define `host`, `port`, `database`, `user`, `password` do PostgreSQL (replicados em variaveis de ambiente pelo servico).
   - `[env]` aceita variaveis adicionais (por exemplo outros ajustes de captura).
2. (Opcional) Crie/ative o ambiente virtual e instale as dependencias.
3. Execute com privilegios de administrador:
   ```cmd
   scripts\install_windows_service.bat
   ```
   O script instala e inicia o servico `ProjetoSacaria`.
4. Para parar/remover:
   ```cmd
   scripts\uninstall_windows_service.bat
   ```
5. Logs padrao do servico ficam em `logs\service.out` e `logs\service.err`. Ajuste o caminho na secao `[paths]` do INI se desejar.

### Checklist de instalacao em um novo servidor

1. Instalar Python 3.x (x64) e adicionar ao PATH.
2. Clonar/copiar o repositorio para o diretorio final (ex.: `C:\workspace\python\projeto_sacaria_yolo5`).
3. Criar `venv` e instalar dependencias com `pip install -r requirements.txt`.
4. Criar o banco e configurar a secao `[database]` do `windows_service.ini`.
5. Ajustar `[server]` e `[paths]` conforme o ambiente.
6. Executar `scripts\install_windows_service.bat` como administrador.
7. Configurar em cada TC a pasta de snapshots e demais parametros.
8. Validar logs em `logs\service.out` e `logs\service.err`.
9. Abrir a porta configurada no firewall ou load balancer, se necessario.
## Estrutura principal do projeto

```
app.py                    # Entrada Flask (create_app + run)
config.py                 # Configuracao inicial das TCs
services/                 # Camada de servicos (detector, banco, repositorios, runtime)
routes/                   # Blueprints Flask (tc, logs, auth, administracao)
templates/                # Templates Jinja2 (painel web)
scripts/                  # Utilitarios (instalacao do servico, testes)
windows_service.py/.ini   # Definicao e configuracao do servico Windows
third_party/yolov5/       # Repositorio YOLOv5 localizado
logs/                     # Logs do servico/Waitress (configuravel)
```

## Documentacao complementar

- Arquitetura geral: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- Dependencias: [`requirements.txt`](requirements.txt).
- Utilitarios: pasta [`scripts/`](scripts/).

Consulte os logs do servico ou as configuracoes das TCs para diagnosticar eventuais problemas de captura.
