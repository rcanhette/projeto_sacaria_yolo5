# routes/logs.py
from flask import send_file
from io import BytesIO
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from flask import Blueprint, render_template, request, redirect, url_for, Response, flash
from services.ct_repository import list_cts, get_ct
from services.session_repository import list_sessions_by_ct, get_session, get_session_logs

logs_bp = Blueprint("logs", __name__)

def _first_ct_id():
    cts = list_cts()
    return cts[0]["id"] if cts else None

@logs_bp.route("/logs")
def logs_panel():
    # ct_id por querystring; se não vier, usa a primeira CT disponível
    try:
        ct_id = int(request.args.get("ct_id") or 0)
    except Exception:
        ct_id = 0

    if not ct_id:
        ct_id = _first_ct_id()
        if not ct_id:
            flash("Nenhuma CT cadastrada. Cadastre uma em /ct-admin.", "error")
            return redirect(url_for("ct_admin.ct_admin_list"))

    ct_row = get_ct(ct_id)
    if not ct_row:
        flash("CT não encontrada.", "error")
        return redirect(url_for("ct_admin.ct_admin_list"))

    cts = list_cts()
    sessions = list_sessions_by_ct(ct_id, limit=200)

    return render_template("logs_panel.html", cts=cts, current_ct=ct_row, sessions=sessions)

@logs_bp.route("/logs/session/<int:session_id>")
def session_logs_view(session_id):
    sess = get_session(session_id)
    if not sess:
        flash("Sessão não encontrada.", "error")
        return redirect(url_for("logs.logs_panel"))

    logs = get_session_logs(session_id)
    return render_template("session_logs.html", sess=sess, logs=logs)

@logs_bp.route("/logs/session/<int:session_id>/export.xlsx")
def session_logs_export_xlsx(session_id):
    from datetime import datetime

    sess = get_session(session_id)
    if not sess:
        return ("Sessão não encontrada", 404)

    logs = get_session_logs(session_id) or []

    # calcula total (se não houver em session, usa último total_atual)
    total_final = sess.get("total_sacarias")
    if total_final is None:
        total_final = logs[-1]["total_atual"] if logs else 0

    wb = Workbook()
    ws = wb.active
    ws.title = "Log da Sessão"

    # ===== Estilos =====
    bold = Font(bold=True)
    title_font = Font(bold=True, size=12)
    header_fill = PatternFill("solid", fgColor="004A80")   # azul coonagro
    header_font = Font(bold=True, color="FFFFFF")
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ===== Cabeçalho superior (como sua imagem) =====
    # Linha 1: CT | <nome>
    ws["A1"] = "CT";                ws["A1"].font = title_font
    ws["B1"] = sess.get("ct_name", "")
    # Linha 2: DATA INICIO | dd/mm/yyyy hh:mm
    ws["A2"] = "DATA INICIO";       ws["A2"].font = bold
    ws["B2"] = sess["data_inicio"].strftime("%d/%m/%Y %H:%M") if sess.get("data_inicio") else "-"
    # Linha 3: DATA FIM | dd/mm/yyyy hh:mm
    ws["A3"] = "DATA FIM";          ws["A3"].font = bold
    ws["B3"] = sess["data_fim"].strftime("%d/%m/%Y %H:%M") if sess.get("data_fim") else "-"
    # Linha 4: TOTAL SACARIA | <n>
    ws["A4"] = "TOTAL";     ws["A4"].font = bold
    ws["B4"] = total_final

    # Linha 5: vazia
    ws.append([])

    # ===== Tabela =====
    start_row = 6
    headers = ["Status", "Hora", "Delta (+1/-1)", "Total Atual"]
    ws.append(headers)
    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    # Linhas da tabela
    row = start_row + 1
    for r in logs:
        status_txt = "RECONHECIMENTO"
        hora_txt = r["ts"].strftime("%H:%M:%S") if r.get("ts") else ""
        ws.cell(row=row, column=1, value=status_txt).alignment = left
        ws.cell(row=row, column=2, value=hora_txt).alignment = center
        ws.cell(row=row, column=3, value=r.get("delta", 0)).alignment = center
        ws.cell(row=row, column=4, value=r.get("total_atual", 0)).alignment = center
        # bordas
        for c in range(1, 5):
            ws.cell(row=row, column=c).border = border
        row += 1

    # ===== Ajuste de larguras =====
    widths = {
        1: 18,   # Status
        2: 14,   # Hora
        3: 16,   # Delta (+1/-1)
        4: 14,   # Total Atual
    }
    # Colunas A/B do cabeçalho
    ws.column_dimensions[get_column_letter(1)].width = max(widths[1], 16)
    ws.column_dimensions[get_column_letter(2)].width = max(widths[2], 24)
    # Se quiser uma coluna C/D no topo melhor dimensionada:
    ws.column_dimensions[get_column_letter(3)].width = widths[3]
    ws.column_dimensions[get_column_letter(4)].width = widths[4]

    # destaque leve nos rótulos do topo
    for r in (1,2,3,4):
        ws[f"A{r}"].alignment = left
        ws[f"B{r}"].alignment = left

    # ===== Retorno do arquivo =====
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"session_{sess['id']}_ct{sess['ct_id']}_{sess.get('lote','')}.xlsx".replace(" ", "_")
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@logs_bp.route("/logs/session/<int:session_id>/export.csv")
def session_logs_export_csv(session_id):
    sess = get_session(session_id)
    if not sess:
        return "Sessão não encontrada", 404

    logs = get_session_logs(session_id)

    def gen_csv():
        # cabeçalho
        yield "session_id,ct_id,ct_name,lote,ts,delta,total_atual\n"
        for r in logs:
            # formata timestamp ISO; Excel abre bem .csv
            ts_str = r["ts"].strftime("%Y-%m-%d %H:%M:%S")
            yield f"{sess['id']},{sess['ct_id']},{sess['ct_name']},{sess['lote']},{ts_str},{r['delta']},{r['total_atual']}\n"

    filename = f"session_{sess['id']}_ct{sess['ct_id']}_{sess['lote']}.csv".replace(" ", "_")
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/csv; charset=utf-8"
    }
    return Response(gen_csv(), headers=headers)

# (Opcional) exportar texto no formato do .txt antigo
@logs_bp.route("/logs/session/<int:session_id>/export.txt")
def session_logs_export_txt(session_id):
    sess = get_session(session_id)
    if not sess:
        return "Sessão não encontrada", 404

    logs = get_session_logs(session_id)

    def gen_txt():
        # Cabeçalho similar ao que você usava
        yield f"LOTE: {sess['lote']}\n"
        data_ini = sess['data_inicio'].strftime('%d/%m/%Y %H:%M:%S')
        yield f"DATA INICIO: {data_ini}\n"
        if sess['data_fim']:
            yield f"DATA FIM: {sess['data_fim'].strftime('%d/%m/%Y %H:%M:%S')}\n"
        yield "\n"
        # Linhas de reconhecimento
        for r in logs:
            hhmmss = r["ts"].strftime("%H:%M:%S")
            if r["delta"] == 1:
                yield f"RECONHECIMENTO + 1 HORA: {hhmmss} TOTAL: {r['total_atual']}\n"
            else:
                yield f"RECONHECIMENTO - 1 HORA: {hhmmss} TOTAL: {r['total_atual']}\n"

    filename = f"session_{sess['id']}_ct{sess['ct_id']}_{sess['lote']}.txt".replace(" ", "_")
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "text/plain; charset=utf-8"
    }
    return Response(gen_txt(), headers=headers)
