# Fila Local Durável do Agente e Endpoints

O Agente mantém uma fila local durável (SQLite) para registrar sessões e eventos quando o Central está indisponível e replicar assim que a conexão voltar. Também registra um trilho de auditoria (journaling) mesmo quando online.

## Como funciona

- Sessões iniciadas online: o Agente cria uma “sombra” local apontando para a sessão remota; eventos são marcados como enviados (sent=1) localmente e enviados ao Central com `event_id` (idempotentes).
- Sessões iniciadas offline: o Agente cria uma sessão local (ID negativo) e enfileira os eventos e o finish. Ao reconectar, cria a sessão remota e replica todos os eventos/finish.

## Local dos dados

- Arquivo: `logs/agent_queue.db` (SQLite)
- Tabelas principais:
  - `local_session(local_id, tc_id, lote, contagem_alvo, created_at_ms, status, total_final, observacao, finished_at_ms, sent_finish, remote_session_id)`
  - `local_event(event_id, local_session_id, delta, total, ts_ms, sent)`

## Endpoints do Agente (HTTP)

- `GET /api/agent/v1/health`
  - pending: `events_pending`, `sessions_without_remote`, `sessions_finish_pending`,
    `last_sync_ms`, `last_compact_ms`, `last_sync_age_ms`, `last_compact_age_ms`, `last_sync_iso`, `last_compact_iso`
  - current_session: `online`, `remote_session_id`, `local_shadow_id`, `count`, `lote`
  - severity: `ok | atencao | critico` (thresholds configuráveis em `agent.ini`)

- `GET /api/agent/v1/pending?limit=50`
  - `sessions_without_remote_list`
  - `sessions_finish_pending_list`
  - `events_sample` (primeiros N eventos pendentes, em ordem cronológica)

- `POST /api/agent/v1/sync`
  - Executa uma rodada de sincronização imediata; retorna contadores `before/after` e `last_sync_ms`.

- `POST /api/agent/v1/compact` (corpo opcional `{ "hard": true }`)
  - Limpa eventos marcados como enviados e sessões finalizadas com finish enviado (sem pendências). `hard=true` tenta `VACUUM`.

## Thresholds de severidade (agent.ini)

Na seção `[agent]` do `agent.ini`:
```
severity_warn_events=1
severity_crit_events=100
severity_warn_sessions=1
severity_crit_sessions=3
severity_crit_last_sync_ms=900000  # 15 minutos
```

## Idempotência no Central

O Central aceita `event_id` em `/api/agent/v1/session/update` e ignora duplicados via índice único parcial em `session_log(event_id) WHERE event_id IS NOT NULL`.

