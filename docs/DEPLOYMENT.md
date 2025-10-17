Deploy de Servidor Central e Agentes (Passo a Passo)

Visão geral
- Central: roda o Flask (front + API + DB) em uma máquina/servidor (porta 8080).
- Agente Local: roda em cada PC do ponto de captura (porta 9090), processa a câmera e envia eventos ao central.

Pré-requisitos
- Windows 10/11 ou Windows Server, com Python 3.x (64 bits) instalado e no PATH.
- PostgreSQL acessível para o servidor central (host, database, user e password).
- Rede liberada entre central (porta 8080) e agentes (porta 9090).

Ambiente virtual (venv)
- Recomenda-se criar e ativar um venv tanto no Central quanto no Agente antes de instalar dependências e serviços:
  - `python -m venv venv`
  - `venv\\Scripts\\activate`
  - `pip install -r requirements.txt`

1) Instalação do Servidor Central
1. Baixe/clene o repositório para o diretório final (ex.: C:\workspace\python\projeto_sacaria_yolo5).
2. (Opcional) Crie um ambiente virtual e ative:
   - `python -m venv venv`
   - `venv\Scripts\activate`
3. Instale dependências: `pip install -r requirements.txt`.
4. Configure acesso ao PostgreSQL via variáveis de ambiente (PGHOST, PGDATABASE, PGUSER, PGPASSWORD) ou mantenha os padrões (localhost).
5. Execute em modo console para validar: `python app.py` (escuta em 0.0.0.0:8080).
6. Acesse o front no navegador e cadastre as TCs (nome, URL RTSP, modelo, ROI etc.).
7. (Opcional) Instale como serviço Windows via NSSM (Central):
   - Crie `central.ini` a partir de `central.ini.example` e ajuste as seções `[database]` e `[server]`.
   - (Opcional) Defina `CENTRAL_INI=C:\caminho\central.ini`.
   - Rode `scripts\install_windows_service_nssm.bat` como administrador.
   - Start: `nssm start ProjetoSacaria_v1`.

2) Instalação do Agente Local (por ponto/PC)
1. No PC do ponto, prepare Python 3.x e `pip`.
2. Copie o projeto (ou parte necessária) e crie venv: `python -m venv venv && venv\Scripts\activate`.
3. Instale dependências: `pip install -r requirements.txt`.
4. Configure `agent.ini` (no PC do ponto):
   - [agent] `tc_id=<ID da TC>` (ex.: 1), `central_url=http://<SERVIDOR>:8080`, `id=pc1-ct1`. O campo `token` pode ficar vazio por enquanto.
   - Observação: URL/ROI/modelo/offsets são definidos no cadastro da TC no servidor central; o agente busca essa configuração automaticamente.
5. Execute o agente em console: `python agent_app.py` (porta 9090).
6. Verifique no dashboard do central: a TC deverá exibir “Agente Online” após ~10s (heartbeat).
7. (Opcional) Instale como serviço Windows via NSSM (Agente):
   - Rode `scripts\install_agent_nssm.bat` como administrador.
   - Start: `nssm start ProjetoSacaria_Agent_<id>`.

3) Rede e portas
- Central: 8080/TCP aberto para navegadores e para os agentes.
- Agente: 9090/TCP aberto para o servidor central (start/stop remoto).
- Verifique firewall do Windows e de rede.

4) Operação e testes
- Dashboard: indica “Agente Online/Offline” e permite start/stop como antes (remoto ou local).
- Teste rápido do agente (no cadastro da TC): botão “Testar agente” executa um ping HTTP.
- SSE (tempo real): segue igual — os eventos do agente atualizam o painel via shadow.

5) Troubleshooting
- Central sem ver agente online:
  - Confirmar que o agente está rodando e que envia heartbeat (logs do agente).
  - Checar porta 8080 acessível do PC do ponto (telnet/curl).
  - Checar `tc_id` correto no `windows_service.ini` do agente.
- Start/Stop remoto falhando:
  - Checar porta 9090 do PC do ponto acessível do servidor central.
  - Ver logs do agente e do central (pasta logs/).
- Banco de dados:
  - Verifique credenciais e conectividade. Use `psql` e teste leitura/escrita.

6) Segurança (planejado)
- Tokens do agente (desligado por enquanto). Futuro: amarrar agente à TC e exigir Bearer token.
- Autorização de comandos no agente (allowlist IP/token no endpoint local).
- TLS entre central e agentes (reverso com proxy e certificados).

7) Backup/Restore
- Backup: `pg_dump -h <host> -U <user> -d contagem_sacaria -F c -f backup.dump`.
- Restore: `pg_restore -h <host> -U <user> -d contagem_sacaria -c backup.dump`.
