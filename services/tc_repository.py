from services.db import query_all, query_one, execute, execute_returning

def list_tcs():
    return query_all("SELECT id, name, source_path, roi, model_path FROM tc ORDER BY id")

def get_tc(tc_id:int):
    return query_one("SELECT id, name, source_path, roi, model_path FROM tc WHERE id=%s", [tc_id])

def create_tc(name:str, source_path:str, roi:str, model_path:str) -> int:
    return execute_returning(
        "INSERT INTO tc (name, source_path, roi, model_path) VALUES (%s,%s,%s,%s) RETURNING id",
        [name, source_path, roi, model_path]
    )

def update_tc(tc_id:int, name:str, source_path:str, roi:str, model_path:str):
    execute(
        "UPDATE tc SET name=%s, source_path=%s, roi=%s, model_path=%s WHERE id=%s",
        [name, source_path, roi, model_path, tc_id]
    )

def delete_tc(tc_id:int):
    execute("DELETE FROM tc WHERE id=%s", [tc_id])

# --------- usados no bootstrap ---------
def count_tcs() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM tc")
    return int(row["n"]) if row else 0

def seed_tcs_from_config(tc_list: dict):
    if not tc_list:
        return
    if count_tcs() > 0:
        return
    for tc in tc_list.values():
        name = (tc.get("name") or f"TC {tc.get('id','')}").strip()
        source_path = (tc.get("source_path") or "").strip()
        roi = (tc.get("roi") or "").strip()
        model_path = (tc.get("model_path") or "sacaria_yolov5n.pt").strip()
        create_tc(name, source_path, roi, model_path)

