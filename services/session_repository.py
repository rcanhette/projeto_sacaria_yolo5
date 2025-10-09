# services/session_repository.py
from typing import Optional, List, Dict
from services.db import execute_returning, execute, query_all, query_one

# -----------------------------------------------------------------------------
# Criação e logs
# -----------------------------------------------------------------------------
def create_session(ct_id: int, lote: str) -> int:
    """Cria sessão ativa para a CT, mas é idempotente: se já existir uma sessão
    'ativo' para a mesma CT, retorna o id existente ao invés de criar outra.
    """
    # 1) Se já existe sessão ativa dessa CT, retorna o id para evitar duplicidade
    existing = query_one(
        """
        SELECT id
          FROM session
         WHERE ct_id = %s AND status = 'ativo'
         ORDER BY data_inicio DESC
         LIMIT 1
        """,
        [ct_id],
    )
    if existing and existing.get("id"):
        return int(existing["id"])

    # 2) Cria nova sessão ativa
    sql = """
        INSERT INTO session (ct_id, lote, data_inicio, status)
        VALUES (%s, %s, NOW(), 'ativo')
        RETURNING id
    """
    return execute_returning(sql, [ct_id, lote])

def insert_log(session_id: int, ct_id: int, delta: int, total_atual: int) -> None:
    sql = """
        INSERT INTO session_log (session_id, ct_id, ts, delta, total_atual)
        VALUES (%s, %s, NOW(), %s, %s)
    """
    execute(sql, [session_id, ct_id, delta, total_atual])

# -----------------------------------------------------------------------------
# Finalização
# -----------------------------------------------------------------------------
def finish_session(session_id: int, total_final: int, status: str = "finalizado") -> None:
    """
    Finaliza a sessão pelo ID:
      - Define data_fim = NOW(), total_final e status.
      - Se não atualizar nenhuma linha (sessão não encontrada, p.ex.),
        faz um fallback: finaliza a sessão ATIVA mais recente da mesma CT.
      - IMPORTANTE: o fallback usa subconsulta no WHERE para evitar
        o erro de ORDER BY em UPDATE no PostgreSQL.
    """
    # 1) tenta finalizar pela PK
    updated = execute(
        """
        UPDATE session
           SET data_fim   = NOW(),
               total_final = %s,
               status      = %s
         WHERE id = %s
        """,
        [total_final, status, session_id],
    )

    # Se o execute() não retorna contagem, tentamos descobrir pela CT do ID dado
    # e fazemos o fallback de forma segura com subconsulta.
    try:
        rowcount = getattr(updated, "rowcount", None)
    except Exception:
        rowcount = None

    if rowcount in (0, None):
        # Descobre a CT dessa sessão
        s = query_one("SELECT ct_id FROM session WHERE id = %s", [session_id])
        if s and "ct_id" in s and s["ct_id"] is not None:
            # 2) fallback: finaliza a sessão ativa mais recente da CT via subconsulta
            execute(
                """
                UPDATE session
                   SET data_fim   = NOW(),
                       total_final = %s,
                       status      = %s
                 WHERE id = (
                     SELECT id
                       FROM session
                      WHERE ct_id = %s
                        AND status = 'ativo'
                      ORDER BY data_inicio DESC
                      LIMIT 1
                 )
                """,
                [total_final, status, s["ct_id"]],
            )

def finish_latest_active_by_ct(ct_id: int, total_final: int, status: str = "finalizado") -> None:
    """
    Finaliza diretamente a sessão ATIVA mais recente de uma CT.
    Útil quando você só tem o ct_id.
    """
    execute(
        """
        UPDATE session
           SET data_fim   = NOW(),
               total_final = %s,
               status      = %s
         WHERE id = (
             SELECT id
               FROM session
              WHERE ct_id = %s
                AND status = 'ativo'
              ORDER BY data_inicio DESC
              LIMIT 1
         )
        """,
        [total_final, status, ct_id],
    )

# -----------------------------------------------------------------------------
# Consultas auxiliares
# -----------------------------------------------------------------------------
def list_sessions_by_ct(ct_id: int, limit: int = 200) -> List[Dict]:
    sql = """
        SELECT id, ct_id, lote, data_inicio, data_fim, total_final, status
          FROM session
         WHERE ct_id = %s
         ORDER BY data_inicio DESC
         LIMIT %s
    """
    return query_all(sql, [ct_id, limit])

def get_session(session_id: int) -> Optional[Dict]:
    sql = """
        SELECT s.id, s.ct_id, s.lote, s.data_inicio, s.data_fim, s.total_final, s.status,
               c.name AS ct_name
          FROM session s
          JOIN ct c ON c.id = s.ct_id
         WHERE s.id = %s
    """
    return query_one(sql, [session_id])

def get_session_logs(session_id: int) -> List[Dict]:
    sql = """
        SELECT id, ts, delta, total_atual
          FROM session_log
         WHERE session_id = %s
         ORDER BY ts ASC
    """
    return query_all(sql, [session_id])

def get_active_session_by_ct(ct_id: int) -> Optional[Dict]:
    sql = """
        SELECT id, ct_id, lote, data_inicio, status, total_final
          FROM session
         WHERE ct_id = %s AND status = 'ativo'
         ORDER BY data_inicio DESC
         LIMIT 1
    """
    return query_one(sql, [ct_id])
