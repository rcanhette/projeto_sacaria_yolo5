from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.db import query_all, query_one, execute_returning, execute


def list_layouts() -> List[Dict[str, Any]]:
    """
    Lista todas as telas configuradas para o painel grande.
    Inclui nomes das TCs quando disponíveis.
    """
    sql = """
        SELECT
            l.id,
            l.name,
            l.left_tc_id,
            l.right_tc_id,
            lt.name AS left_name,
            rt.name AS right_name
        FROM tc_wall_layout l
        LEFT JOIN tc lt ON lt.id = l.left_tc_id
        LEFT JOIN tc rt ON rt.id = l.right_tc_id
        ORDER BY l.id;
    """
    return list(query_all(sql))


def get_layout(layout_id: int) -> Optional[Dict[str, Any]]:
    """
    Busca uma tela específica pelo id.
    """
    sql = """
        SELECT
            l.id,
            l.name,
            l.left_tc_id,
            l.right_tc_id,
            lt.name AS left_name,
            rt.name AS right_name
        FROM tc_wall_layout l
        LEFT JOIN tc lt ON lt.id = l.left_tc_id
        LEFT JOIN tc rt ON rt.id = l.right_tc_id
        WHERE l.id = %s;
    """
    return query_one(sql, [layout_id])


def create_layout(
    name: str,
    left_tc_id: Optional[int],
    right_tc_id: Optional[int],
) -> int:
    """
    Cria uma nova tela de painel grande.
    """
    sql = """
        INSERT INTO tc_wall_layout (name, left_tc_id, right_tc_id)
        VALUES (%s, %s, %s)
        RETURNING id;
    """
    return int(execute_returning(sql, [name, left_tc_id, right_tc_id]))


def delete_layout(layout_id: int) -> None:
    """
    Remove uma tela configurada.
    """
    execute("DELETE FROM tc_wall_layout WHERE id = %s;", [layout_id])


def update_layout(
    layout_id: int,
    name: str,
    left_tc_id: Optional[int],
    right_tc_id: Optional[int],
) -> None:
    """
    Atualiza uma tela existente do painel grande.
    """
    sql = """
        UPDATE tc_wall_layout
           SET name = %s,
               left_tc_id = %s,
               right_tc_id = %s
         WHERE id = %s;
    """
    execute(sql, [name, left_tc_id, right_tc_id, layout_id])
