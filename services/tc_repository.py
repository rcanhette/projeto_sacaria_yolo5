from services.db import query_all, query_one, execute, execute_returning

def list_tcs():
    return query_all(
        "SELECT id, name, source_path, roi, model_path, line_offset_red, line_offset_blue, flow_mode, "
        "max_lost, match_dist, min_conf, missed_frame_dir "
        "FROM tc ORDER BY id"
    )

def get_tc(tc_id:int):
    return query_one(
        "SELECT id, name, source_path, roi, model_path, line_offset_red, line_offset_blue, flow_mode, "
        "max_lost, match_dist, min_conf, missed_frame_dir "
        "FROM tc WHERE id=%s",
        [tc_id],
    )

def create_tc(name:str, source_path:str, roi:str, model_path:str,
              line_offset_red:int = 40, line_offset_blue:int = -40,
              flow_mode:str = "cima", max_lost:int = 2,
              match_dist:float = 150, min_conf:float = 0.8,
              missed_frame_dir:str | None = None) -> int:
    if max_lost < 0:
        max_lost = 0
    match_dist = int(round(match_dist))
    if match_dist <= 0:
        match_dist = 1
    if min_conf < 0:
        min_conf = 0.0
    if min_conf > 1:
        min_conf = 1.0
    dir_path = (missed_frame_dir or "").strip()
    return execute_returning(
        "INSERT INTO tc (name, source_path, roi, model_path, line_offset_red, line_offset_blue, flow_mode, "
        "max_lost, match_dist, min_conf, missed_frame_dir) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        [name, source_path, roi, model_path, line_offset_red, line_offset_blue,
         flow_mode, max_lost, match_dist, min_conf, dir_path]
    )

def update_tc(tc_id:int, name:str, source_path:str, roi:str, model_path:str,
              line_offset_red:int = 40, line_offset_blue:int = -40,
              flow_mode:str = "cima", max_lost:int = 2,
              match_dist:float = 150, min_conf:float = 0.8,
              missed_frame_dir:str | None = None):
    if max_lost < 0:
        max_lost = 0
    match_dist = int(round(match_dist))
    if match_dist <= 0:
        match_dist = 1
    if min_conf < 0:
        min_conf = 0.0
    if min_conf > 1:
        min_conf = 1.0
    dir_path = (missed_frame_dir or "").strip()
    execute(
        "UPDATE tc SET name=%s, source_path=%s, roi=%s, model_path=%s, "
        "line_offset_red=%s, line_offset_blue=%s, flow_mode=%s, "
        "max_lost=%s, match_dist=%s, min_conf=%s, missed_frame_dir=%s WHERE id=%s",
        [name, source_path, roi, model_path, line_offset_red, line_offset_blue,
         flow_mode, max_lost, match_dist, min_conf, dir_path, tc_id]
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
        try:
            red = int(tc.get("line_offset_red", 40))
        except Exception:
            red = 40
        try:
            blue = int(tc.get("line_offset_blue", -40))
        except Exception:
            blue = -40
        flow = (tc.get("flow_mode") or "cima").strip().lower()
        if flow not in ("cima", "baixo", "sem_fluxo"):
            flow = "cima"
        try:
            max_lost = int(tc.get("max_lost", 2))
        except Exception:
            max_lost = 2
        try:
            match_dist = int(tc.get("match_dist", 150))
        except Exception:
            match_dist = 150
        try:
            min_conf = float(tc.get("min_conf", 0.8))
        except Exception:
            min_conf = 0.8
        create_tc(name, source_path, roi, model_path, red, blue, flow, max_lost, match_dist, min_conf, (tc.get("missed_frame_dir") or "").strip())
