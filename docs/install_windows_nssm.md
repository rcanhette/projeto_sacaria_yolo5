# Instalação Central e Agent (Windows + NSSM)

Passo a passo resumido para rodar o sistema localmente (YOLO já embarcado) e registrar serviços com NSSM.

## Pré-requisitos
- Windows 10/11 ou Server com PowerShell.
- Python 3.10+ instalado (e no PATH) ou use o python embutido na pasta do sistema.
- NSSM instalado (nssm.exe acessível).
- Git não é necessário em produção; os arquivos vêm prontos em `dist/central` e `dist/agent`.

> Pesos YOLO já estão incluídos (`sacaria_yolov5n.pt` e `best.pt`) na pasta do Agent. Não baixa nada em tempo de execução.

## 1) Central
1. Copie a pasta `dist/central` para o destino, ex.: `C:\sistema_sacaria\central`.
2. No PowerShell:
   ```powershell
   cd C:\sistema_sacaria\central
   python -m venv venv
   .\venv\Scripts\pip install --upgrade pip
   .\venv\Scripts\pip install -r requirements-central.txt
   ```
3. Configure `central.ini` e variáveis de ambiente conforme seu banco (PostgreSQL).
4. Registrar serviço com NSSM (rodar em prompt elevado):
   ```powershell
   nssm install Central_TC "C:\sistema_sacaria\central\venv\Scripts\python.exe" ^
     "-m" "waitress" "--host" "0.0.0.0" "--port" "8000" "--call" "app:create_app"
   nssm set Central_TC AppDirectory C:\sistema_sacaria\central
   nssm set Central_TC AppEnvironmentExtra PYTHONIOENCODING=utf-8
   nssm set Central_TC AppEnvironmentExtra PYTHONUNBUFFERED=1
   nssm set Central_TC AppEnvironmentExtra PYTHONPATH=C:\sistema_sacaria\central
   nssm start Central_TC
   ```

## 2) Agent
1. Copie a pasta `dist/agent` para o destino, ex.: `C:\sistema_sacaria\agent_TC1`.
2. No PowerShell:
   ```powershell
   cd C:\sistema_sacaria\agent_TC1
   python -m venv venv
   .\venv\Scripts\pip install --upgrade pip
   .\venv\Scripts\pip install -r requirements-agent.txt
   ```
3. Ajuste `agent.ini` (rota RTSP/arquivo, ID da TC, host da central, etc.).
4. Registrar serviço com NSSM (prompt elevado). Escolha uma porta por TC (ex.: 9090, 9091):
   ```powershell
   nssm install Agent_TC1 "C:\sistema_sacaria\agent_TC1\venv\Scripts\waitress-serve.exe" ^
     "--host" "0.0.0.0" "--port" "9090" "--call" "agent_app:create_agent_app"
   nssm set Agent_TC1 AppDirectory C:\sistema_sacaria\agent_TC1
   nssm set Agent_TC1 AppEnvironmentExtra PYTHONIOENCODING=utf-8
   nssm set Agent_TC1 AppEnvironmentExtra PYTHONUNBUFFERED=1
   nssm set Agent_TC1 AppEnvironmentExtra PYTHONPATH=C:\sistema_sacaria\agent_TC1
   nssm start Agent_TC1
   ```

## 3) Observações
- Se usar GPU, instale Torch com CUDA antes do `requirements-agent` e aponte o modelo para a GPU (padrão é CPU).
- Para reduzir CPU em agentes somente CPU, você pode limitar threads BLAS no serviço (opcional):
  `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `NUMEXPR_MAX_THREADS=1` em AppEnvironmentExtra.
- Se aparecer aviso de “dubious ownership” em `third_party/yolov5`, marque a pasta como segura ou remova o `.git` dali:
  ```powershell
  git config --global --add safe.directory C:/sistema_sacaria/agent_TC1/third_party/yolov5
  ```
- Reinicie os serviços após qualquer mudança em configs ou dependências.
