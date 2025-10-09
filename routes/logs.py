# routes/logs.py
from flask import Blueprint, render_template, request, abort, Response, send_file
from io import BytesIO
import re

# Auth/session
from routes.auth import login_required, current_user
from services.auth_repository import user_can_view_tc

# DB helpers
from services.db import query_all, query_one
from services.tc_repository import list_tcs

# Excel
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side


logs_bp = Blueprint("logs", __name__, url_prefix="")

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _permitted_cts():
    u = current_user()
    if not u:
        abort(401)
    all_tcs = list_tcs()
    permitted = [c for c in all_tcs if user_can_view_tc(u, c["id"])]
    return u, permitted


def _get_session_or_404(session_id: int):
    s = query_one(
        """
        SELECT s.id, s.ct_id, s.lote, s.data_inicio, s.data_fim, s.status,
               COALESCE(s.total_final, 0) AS total_final,
               c.name AS ct_name
          FROM session s
          JOIN ct c ON c.id = s.ct_id
         WHERE s.id = %s
        """,
        [session_id],
    )
    if not s:
        abort(404)
    _, permitted_cts = _permitted_cts()
    permitted_ids = {c["id"] for c in permitted_cts}
    if s["ct_id"] not in permitted_ids:
        abort(403)
    return s


def _get_session_logs(session_id: int):
    return query_all(
        """
        SELECT id, ts, delta, total_atual
          FROM session_log
         WHERE session_id = %s
         ORDER BY ts ASC
        """,
        [session_id],
    )


# Excel sheet titles cannot contain: : \ / ? * [ ]
# and must be <= 31 chars
def _safe_sheet_title(name: str) -> str:
    if not name:
        return "Planilha"
    bad = r'[:\\/*?\[\]/]'   # inclui a barra /
    cleaned = re.sub(bad, " ", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned or "Planilha")[:31]


def _safe_filename_piece(text: str) -> str:
    if not text:
        return ""
    piece = re.sub(r'[^0-9A-Za-z_\-]+', '_', text.strip())
    piece = re.sub(r'_+', '_', piece).strip('_')
    return piece


# ----------------------------------------------------------------------
# LISTA DE SESSÕES (com filtro por CT)
# ----------------------------------------------------------------------
@logs_bp.get("/logs")
@login_required
def logs_panel():
    """
    Mostra as sessões das CTs às quais o usuário tem acesso.
    Filtro opcional: ?ct_id=<id> ou ?ct_id=all
    """
    _, permitted_cts = _permitted_cts()
    if not permitted_cts:
        return render_template("logs_panel.html", cts=[], current_ct_id="all", sessions=[])

    ct_id_arg = request.args.get("ct_id", "all")
    permitted_ids = [c["id"] for c in permitted_cts]

    if ct_id_arg == "all":
        ct_ids = permitted_ids
        current_ct_id = "all"
    else:
        try:
            wanted = int(ct_id_arg)
        except ValueError:
            abort(400)
        if wanted not in permitted_ids:
            abort(403)
        ct_ids = [wanted]
        current_ct_id = str(wanted)

    placeholders = ",".join(["%s"] * len(ct_ids))
    sql = f"""
        SELECT
            s.id,
            s.ct_id,
            c.name     AS ct_name,
            s.lote,
            s.data_inicio,
            s.data_fim,
            s.status,
            COALESCE(s.total_final, 0) AS total_final
        FROM session s
          JOIN tc c ON c.id = s.ct_id
        WHERE s.ct_id IN ({placeholders})
        ORDER BY s.data_inicio DESC
        LIMIT 2000
    """
    sessions = query_all(sql, ct_ids)

    return render_template(
        "logs_panel.html",
        cts=permitted_cts,
        current_ct_id=current_ct_id,
        sessions=sessions,
    )


# ----------------------------------------------------------------------
# DETALHE DA SESSÃO (logs)
# ----------------------------------------------------------------------
@logs_bp.get("/logs/session/<int:session_id>")
@login_required
def session_logs(session_id: int):
    s = _get_session_or_404(session_id)
    logs = _get_session_logs(session_id)
    return render_template("session_log.html", sess=s, logs=logs)


# ----------------------------------------------------------------------
# EXPORT XLSX — layout igual ao do seu exemplo
# ----------------------------------------------------------------------
@logs_bp.get("/logs/session/<int:session_id>/export.xlsx")
@login_required
def session_logs_export_xlsx(session_id: int):
    sess = _get_session_or_404(session_id)
    rows = _get_session_logs(session_id) or []

    total_final = sess.get("total_final", 0) or (rows[-1]["total_atual"] if rows else 0)

    wb = Workbook()
    ws = wb.active
    sheet_title = _safe_sheet_title(sess.get("ct_name") or f"TC {sess['ct_id']}")
    ws.title = sheet_title

    # Estilos
    bold = Font(bold=True)
    title_font = Font(bold=True, size=12)
    header_fill = PatternFill("solid", fgColor="004A80")
    header_font = Font(bold=True, color="FFFFFF")
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Cabeçalho superior
    ws["A1"] = "TC";             ws["A1"].font = title_font
    ws["B1"] = sess.get("ct_name") or f"TC {sess['ct_id']}"
    ws["A2"] = "DATA INICIO";    ws["A2"].font = bold
    ws["B2"] = sess["data_inicio"].strftime("%d/%m/%Y %H:%M") if sess.get("data_inicio") else "-"
    ws["A3"] = "DATA FIM";       ws["A3"].font = bold
    ws["B3"] = sess["data_fim"].strftime("%d/%m/%Y %H:%M") if sess.get("data_fim") else "-"
    ws["A4"] = "TOTAL";          ws["A4"].font = bold
    ws["B4"] = total_final

    ws.append([])  # linha vazia

    # Tabela
    start_row = 6
    headers = ["Status", "Hora", "Delta (+1/-1)", "Total Atual"]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=start_row, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    r = start_row + 1
    for item in rows:
        status_txt = "RECONHECIMENTO"
        hora_txt = item["ts"].strftime("%H:%M:%S") if item.get("ts") else ""
        delta_val = item.get("delta", 0)
        total_val = item.get("total_atual", 0)

        ws.cell(row=r, column=1, value=status_txt).alignment = left
        ws.cell(row=r, column=2, value=hora_txt).alignment = center
        ws.cell(row=r, column=3, value=delta_val).alignment = center
        ws.cell(row=r, column=4, value=total_val).alignment = center

        for c in range(1, 5):
            ws.cell(row=r, column=c).border = border
        r += 1

    # larguras de coluna
    widths = {1: 18, 2: 14, 3: 16, 4: 14}
    for col_idx, w in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w
    for rr in (1, 2, 3, 4):
        ws[f"A{rr}"].alignment = left
        ws[f"B{rr}"].alignment = left
    ws.column_dimensions["A"].width = max(16, ws.column_dimensions["A"].width or 0)
    ws.column_dimensions["B"].width = max(24, ws.column_dimensions["B"].width or 0)

    # Nome do arquivo: session_ct<CTID>_<LOTE>.xlsx (sanitizado)
    ct_id = sess["ct_id"]
    lote = _safe_filename_piece(sess.get("lote") or "")
    filename = f"session_tc{ct_id}" + (f"_{lote}" if lote else "") + ".xlsx"

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ----------------------------------------------------------------------
# Export CSV simples (opcional)
# ----------------------------------------------------------------------
@logs_bp.get("/logs/session/<int:session_id>/export.csv")
@login_required
def session_logs_export_csv(session_id: int):
    sess = _get_session_or_404(session_id)
    rows = _get_session_logs(session_id)

    def gen_csv():
        yield "session_id,ct_id,ct_name,lote,ts,delta,total_atual\n"
        for r in rows:
            ts_str = r["ts"].strftime("%Y-%m-%d %H:%M:%S") if r.get("ts") else ""
            ct_name = (sess.get("ct_name") or "").replace(",", " ")
            lote = (sess.get("lote") or "").replace(",", " ")
            yield f"{sess['id']},{sess['ct_id']},{ct_name},{lote},{ts_str},{r.get('delta',0)},{r.get('total_atual',0)}\n"

    lote_piece = _safe_filename_piece(sess.get("lote") or "")
    filename = f"session_tc{sess['ct_id']}" + (f"_{lote_piece}" if lote_piece else "") + ".csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(gen_csv(), headers=headers)
