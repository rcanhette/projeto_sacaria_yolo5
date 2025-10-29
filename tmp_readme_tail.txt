
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

Siga “Instalação via NSSM ? 2) Agente (PC do ponto)”. Abra a porta 9090/TCP no firewall do PC do ponto. Valide no dashboard que o agente aparece como “Agente Online” e que o start/stop remoto funciona.
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
