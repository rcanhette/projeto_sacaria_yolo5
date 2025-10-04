# services/session_repository.py
from services.db import execute_returning, execute, query_all, query_one

def create_session(ct_id:int, lote:str) -> int:
    sql = """
        INSERT INTO session (ct_id, lote, data_inicio, status)
        VALUES (%s, %s, NOW(), 'ativo')
        RETURNING id
    """
    return execute_returning(sql, [ct_id, lote])

def insert_log(session_id:int, ct_id:int, delta:int, total_atual:int):
    sql = """
        INSERT INTO session_log (session_id, ct_id, ts, delta, total_atual)
        VALUES (%s, %s, NOW(), %s, %s)
    """
    execute(sql, [session_id, ct_id, delta, total_atual])

def finish_session(session_id:int, total:int, status:str='finalizado'):
    sql = """
        UPDATE session
           SET data_fim = NOW(),
               total_sacarias = %s,
               status = %s
         WHERE id = %s
    """
    execute(sql, [total, status, session_id])

# ============ NOVAS FUNÇÕES P/ PAINEL ============

def list_sessions_by_ct(ct_id:int, limit:int=200):
    sql = """
        SELECT id, ct_id, lote, data_inicio, data_fim, total_sacarias, status
          FROM session
         WHERE ct_id = %s
         ORDER BY data_inicio DESC
         LIMIT %s
    """
    return query_all(sql, [ct_id, limit])

def get_session(session_id:int):
    sql = """
        SELECT s.id, s.ct_id, s.lote, s.data_inicio, s.data_fim, s.total_sacarias, s.status,
               c.name AS ct_name
          FROM session s
          JOIN ct c ON c.id = s.ct_id
         WHERE s.id = %s
    """
    return query_one(sql, [session_id])

def get_session_logs(session_id:int):
    sql = """
        SELECT id, ts, delta, total_atual
          FROM session_log
         WHERE session_id = %s
         ORDER BY ts ASC
    """
    return query_all(sql, [session_id])
