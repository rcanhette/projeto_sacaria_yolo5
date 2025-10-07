from services.db import query_all, query_one, execute, execute_returning

def list_cts():
    return query_all("SELECT id, name, source_path, roi, model_path FROM ct ORDER BY id")

def get_ct(ct_id:int):
    return query_one("SELECT id, name, source_path, roi, model_path FROM ct WHERE id=%s", [ct_id])

def create_ct(name:str, source_path:str, roi:str, model_path:str) -> int:
    return execute_returning(
        "INSERT INTO ct (name, source_path, roi, model_path) VALUES (%s,%s,%s,%s) RETURNING id",
        [name, source_path, roi, model_path]
    )

def update_ct(ct_id:int, name:str, source_path:str, roi:str, model_path:str):
    execute(
        "UPDATE ct SET name=%s, source_path=%s, roi=%s, model_path=%s WHERE id=%s",
        [name, source_path, roi, model_path, ct_id]
    )

def delete_ct(ct_id:int):
    execute("DELETE FROM ct WHERE id=%s", [ct_id])

# --------- usados no bootstrap ---------
def count_cts() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM ct")
    return int(row["n"]) if row else 0

def seed_cts_from_config(ct_list: dict):
    if not ct_list:
        return
    if count_cts() > 0:
        return
    for ct in ct_list.values():
        name = (ct.get("name") or f"CT {ct.get('id','')}").strip()
        source_path = (ct.get("source_path") or "").strip()
        roi = (ct.get("roi") or "").strip()
        model_path = (ct.get("model_path") or "sacaria_yolov5n.pt").strip()
        create_ct(name, source_path, roi, model_path)
