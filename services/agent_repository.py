from typing import Optional, Dict
from services.db import query_one, execute


def get_agent_by_token(token: str) -> Optional[Dict]:
    sql = """
        SELECT id, agent_id, token, tc_id, active
          FROM agent
         WHERE token = %s AND active = TRUE
    """
    return query_one(sql, [token])


def upsert_status(agent_db_id: int, tc_id: int | None, hostname: str | None, version: str | None, status: str | None) -> None:
    sql = """
        INSERT INTO agent_status (agent_id, last_seen, hostname, version, status, tc_id)
        VALUES (%s, NOW(), %s, %s, %s, %s)
        ON CONFLICT (agent_id)
        DO UPDATE SET last_seen = EXCLUDED.last_seen,
                      hostname  = COALESCE(EXCLUDED.hostname, agent_status.hostname),
                      version   = COALESCE(EXCLUDED.version,  agent_status.version),
                      status    = COALESCE(EXCLUDED.status,   agent_status.status),
                      tc_id     = COALESCE(EXCLUDED.tc_id,    agent_status.tc_id)
    """
    execute(sql, [agent_db_id, hostname, version, status, tc_id])


def upsert_tc_status(tc_id: int, hostname: str | None, version: str | None, status: str | None) -> None:
    sql = """
        INSERT INTO tc_agent_status (tc_id, last_seen, hostname, version, status)
        VALUES (%s, NOW(), %s, %s, %s)
        ON CONFLICT (tc_id)
        DO UPDATE SET last_seen = EXCLUDED.last_seen,
                      hostname  = COALESCE(EXCLUDED.hostname, tc_agent_status.hostname),
                      version   = COALESCE(EXCLUDED.version,  tc_agent_status.version),
                      status    = COALESCE(EXCLUDED.status,   tc_agent_status.status)
    """
    execute(sql, [tc_id, hostname, version, status])


def get_active_agent_for_tc(tc_id: int) -> Optional[Dict]:
    """Retorna agente ativo vinculado à TC (com hostname/status quando houver)."""
    sql = """
        SELECT a.id,
               a.agent_id,
               a.token,
               a.tc_id,
               a.active,
               s.hostname,
               s.version,
               s.last_seen,
               s.status as runtime_status,
               EXTRACT(EPOCH FROM (NOW()::timestamp - s.last_seen)) as age_sec
          FROM agent a
          LEFT JOIN agent_status s ON s.agent_id = a.id
         WHERE a.active = TRUE AND a.tc_id = %s
         LIMIT 1
    """
    return query_one(sql, [tc_id])


def get_tc_status(tc_id: int) -> Optional[Dict]:
    sql = """
        SELECT tc_id,
               last_seen,
               hostname,
               version,
               status,
               EXTRACT(EPOCH FROM (NOW()::timestamp - last_seen)) as age_sec
          FROM tc_agent_status
         WHERE tc_id = %s
    """
    return query_one(sql, [tc_id])


def get_effective_tc_status(tc_id: int) -> Optional[Dict]:
    """Retorna status de TC usando tc_agent_status ou agent_status quando aplicável."""
    row = get_tc_status(tc_id)
    if row:
        return row
    agent = get_active_agent_for_tc(tc_id)
    if not agent:
        return None
    return {
        "tc_id": tc_id,
        "last_seen": agent.get("last_seen"),
        "hostname": agent.get("hostname"),
        "version": agent.get("version"),
        "status": agent.get("runtime_status"),
        "age_sec": agent.get("age_sec"),
    }


def get_host_for_tc(tc_id: int) -> Optional[str]:
    """Resolve o host do agente para uma TC, priorizando status por TC.
    Retorna hostname ou None.
    """
    row = get_tc_status(tc_id)
    if row and row.get("hostname"):
        return row["hostname"]
    agent = get_active_agent_for_tc(tc_id)
    if agent and agent.get("hostname"):
        return agent["hostname"]
    return None
