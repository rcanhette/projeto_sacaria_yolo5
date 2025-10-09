# services/auth_repository.py
from typing import List, Dict, Optional, Iterable, Union
from collections.abc import Mapping
import hashlib
from services.db import query_all, query_one, execute

# Papéis válidos do sistema
VALID_ROLES = ("admin", "supervisor", "operator", "viewer")

# -----------------------------
# Helpers de senha (hash SHA-256)
# -----------------------------
def _hash_password(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

# -----------------------------
# USERS (utilitários – sem seed)
# -----------------------------
def list_users() -> List[Dict]:
    return query_all("SELECT id, username, role, active FROM users ORDER BY id")

def get_user_by_username(username: Union[str, Mapping]) -> Optional[Dict]:
    """
    Nome de usuário pode vir como string ou Mapping (ex.: RealDictRow com 'username').
    """
    if isinstance(username, Mapping):
        username = username.get("username", "")
    username = str(username or "").strip().lower()
    return query_one(
        "SELECT id, username, password, role, active FROM users WHERE username=%s",
        [username]
    )

def get_user_by_id(user_id: int) -> Optional[Dict]:
    return query_one("SELECT id, username, role, active FROM users WHERE id=%s", [user_id])

def create_user(username: str, password: str, role: str, active: bool) -> int:
    """
    Cria usuário sob demanda (ex.: via tela do sistema).
    Não é chamada automaticamente em lugar nenhum.
    """
    if role not in VALID_ROLES:
        raise ValueError("Papel inválido.")
    username = (username or "").strip().lower()
    pwd = _hash_password(password)
    row = query_one(
        "INSERT INTO users (username, password, role, active) VALUES (%s,%s,%s,%s) RETURNING id",
        [username, pwd, role, active],
    )
    return row["id"]

def update_user(user_id: int, username: str, role: str, active: bool) -> None:
    if role not in VALID_ROLES:
        raise ValueError("Papel inválido.")
    username = (username or "").strip().lower()
    execute(
        "UPDATE users SET username=%s, role=%s, active=%s WHERE id=%s",
        [username, role, active, user_id],
    )

def reset_password(user_id: int, new_password: str) -> None:
    pwd = _hash_password(new_password)
    execute("UPDATE users SET password=%s WHERE id=%s", [pwd, user_id])

def delete_user(user_id: int) -> None:
    """
    Exclui usuário sob demanda. Remove vínculos em user_ct antes.
    Nenhuma exclusão automática é feita; só quando você chamar esta função.
    """
    execute("DELETE FROM user_tc WHERE user_id=%s", [user_id])
    execute("DELETE FROM users WHERE id=%s", [user_id])

def verify_password(user_or_username: Union[Dict, Mapping, str], raw_password: str) -> Optional[Dict]:
    """
    Verifica credenciais.
    Aceita:
      - Mapping/dict (row do banco contendo 'password' e 'username'), ou
      - str (username).
    Retorna dict {id, username, role, active} quando válido; senão, None.
    """
    if isinstance(user_or_username, Mapping):
        u = user_or_username  # RealDictRow/dict
    else:
        uname = str(user_or_username or "").strip().lower()
        u = get_user_by_username(uname)

    if not u or not bool(u.get("active", True)):
        return None

    if u.get("password") != _hash_password(raw_password):
        return None

    return {
        "id": int(u["id"]),
        "username": u["username"],
        "role": u["role"],
        "active": bool(u["active"]),
    }

# -----------------------------
# TC ACCESS (vínculos)
# -----------------------------
def list_user_tc_ids(user_id: int) -> List[int]:
    rows = query_all("SELECT tc_id FROM user_tc WHERE user_id=%s ORDER BY tc_id", [user_id])
    return [r["tc_id"] for r in rows]

def set_user_tcs(user_id: int, tc_ids: Iterable[int]) -> None:
    execute("DELETE FROM user_tc WHERE user_id=%s", [user_id])
    for tid in tc_ids:
        execute(
            "INSERT INTO user_tc (user_id, tc_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            [user_id, tid],
        )

def list_users_by_role(roles: Iterable[str]) -> List[Dict]:
    roles = [r for r in roles if r in VALID_ROLES]
    if not roles:
        return []
    placeholders = ",".join(["%s"] * len(roles))
    sql = f"SELECT id, username, role, active FROM users WHERE role IN ({placeholders}) ORDER BY username"
    return query_all(sql, roles)

def list_user_ids_for_tc(tc_id: int) -> List[int]:
    rows = query_all("SELECT user_id FROM user_tc WHERE tc_id=%s ORDER BY user_id", [tc_id])
    return [r["user_id"] for r in rows]

def set_tc_users(tc_id: int, user_ids: Iterable[int]) -> None:
    execute("DELETE FROM user_tc WHERE tc_id=%s", [tc_id])
    for uid in user_ids:
        execute(
            "INSERT INTO user_tc (tc_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            [tc_id, uid],
        )

# -----------------------------
# AUTHZ HELPERS (permissões)
# -----------------------------
def user_can_view_tc(user: Dict, tc_id: int) -> bool:
    if not user or not user.get("active", True):
        return False
    if user["role"] in ("admin", "supervisor"):
        return True
    return tc_id in set(list_user_tc_ids(user["id"]))

def user_can_control_tc(user: Dict, tc_id: int) -> bool:
    if not user_can_view_tc(user, tc_id):
        return False
    return user["role"] in ("admin", "supervisor", "operator")
