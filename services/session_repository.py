from typing import Optional, List, Dict
from services.db import execute_returning, execute, query_all, query_one

# -----------------------------------------------------------------------------
# Criação e logs
# -----------------------------------------------------------------------------
def create_session(ct_id: int, lote: str) -> int:
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
    Finaliza a sessão: grava data_fim, total_final e status com base no ID da sessão.
    Se nada for atualizado (0 linhas), tenta finalizar a sessão ATIVA mais recente da CT
    à qual essa sessão pertence (fallback de segurança).
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

    if getattr(updated, "rowcount", None) in (0, None):
        # 2) fallback: descobrir a CT da sessão informada e finalizar a ativa mais recente
        s = query_one("SELECT ct_id FROM session WHERE id = %s", [session_id])
        if s and "ct_id" in s:
            execute(
                """
                UPDATE session
                   SET data_fim   = NOW(),
                       total_final = %s,
                       status      = %s
                 WHERE ct_id = %s
                   AND status = 'ativo'
                 ORDER BY data_inicio DESC
                 LIMIT 1
                """,
                [total_final, status, s["ct_id"]],
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
