import time
import os
import json
import logging
import requests
from requests.adapters import HTTPAdapter
from flask import Blueprint, render_template, Response, request, redirect, url_for, flash
# Lazy import de CapturePoint dentro de _ensure_cp para evitar carregar Torch
from services.tc_repository import get_tc, list_tcs
from services.session_repository import get_active_session_by_ct
from services.auth_repository import list_user_tc_ids, user_can_view_tc, user_can_control_tc
from services.db import query_one
from services.agent_repository import get_host_for_tc
from services.runtime import tc_runtime, get_or_create_shadow
from routes.auth import current_user, login_required
from services.session_repository import get_active_session_by_ct
from services.tc_wall_repository import list_layouts, get_layout, create_layout, delete_layout, update_layout

tc_bp = Blueprint("tc", __name__)
log = logging.getLogger(__name__)

# Estado global para fan-out de JPEG no modo local
_mjpeg_state = {}

# Cache leve para SSE (por TC) para reduzir consultas por segundo
_sse_db_cache = {}  # { tc_id: { 'ts': epoch_seconds, 'db_row': row, 'db_total_live': int|None } }

# Sessão HTTP com keep-alive para falar com o Agent
_agent_http = requests.Session()
try:
    _agent_http.mount('http://', HTTPAdapter(pool_connections=50, pool_maxsize=50))
    _agent_http.mount('https://', HTTPAdapter(pool_connections=50, pool_maxsize=50))
except Exception:
    pass

# Parâmetros de streaming locais via env (Central)
def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.getenv(name, str(default)))
        return max(lo, min(hi, v))
    except Exception:
        return default

CENTRAL_MJPEG_QUALITY = _env_int("CENTRAL_JPEG_QUALITY", 80, 30, 95)
CENTRAL_MJPEG_FPS = _env_int("CENTRAL_MJPEG_FPS", 25, 1, 60)
CENTRAL_MJPEG_INTERVAL = max(0.01, 1.0 / float(CENTRAL_MJPEG_FPS))

def _parse_roi(roi_val):
    if roi_val is None:
        return None
    if isinstance(roi_val, (tuple, list)) and len(roi_val) == 4:
        return tuple(int(v) for v in roi_val)
    parts = [p.strip() for p in str(roi_val).split(",")]
    if len(parts) != 4:
        return None
    return tuple(int(p) for p in parts)

def _ensure_cp(tc_row):
    tc_id = tc_row["id"]
    if tc_id in tc_runtime:
        return tc_runtime[tc_id]
    # Se existir agente remoto, use shadow e evite Torch no servidor
    try:
        _host = get_host_for_tc(tc_id)
    except Exception:
        _host = None
    if _host:
        cp = get_or_create_shadow(tc_id, name=tc_row.get("name"))
        tc_runtime[tc_id] = cp
        return cp
    log.info(
        "Inicializando CapturePoint para TC %s (%s) com fonte '%s'",
        tc_id,
        tc_row.get("name"),
        tc_row.get("source_path"),
    )
    cfg = {
        "source_type": "rtsp",
        "path": tc_row["source_path"],
        "roi": _parse_roi(tc_row["roi"]),
        "model": tc_row.get("model_path") or "sacaria_yolov5n.pt",
        "line_offset_red": tc_row.get("line_offset_red", 40),
        "line_offset_blue": tc_row.get("line_offset_blue", -40),
        "flow_mode": tc_row.get("flow_mode") or "cima",
        "max_lost": int(tc_row.get("max_lost", 2) or 0),
        "match_dist": float(tc_row.get("match_dist", 150) or 150),
        "min_conf": float(tc_row.get("min_conf", 0.8) or 0.8),
        "missed_frame_dir": (tc_row.get("missed_frame_dir") or "").strip(),
    }
    log.debug("ConfiguraÃÂ§ÃÂ£o completa da TC %s: %s", tc_id, cfg)
    from services.capture_point import CapturePoint
    cp = CapturePoint(tc_row, cfg)
    tc_runtime[tc_id] = cp
    return cp

@tc_bp.route("/tc/<int:tc_id>")
@login_required
def tc_detail(tc_id):
    # Somente admin pode abrir a tela individual
    u = current_user()
    if u["role"] != "admin":
        flash("Acesso negado ÃÂ  tela individual.", "error")
        return redirect(url_for("index"))
    tc_row = get_tc(tc_id)
    if not tc_row:
        flash("TC nÃÂ£o encontrada.", "error")
        return redirect(url_for("index"))
    cp = _ensure_cp(tc_row)
    return redirect(url_for("tc_multi"))

@tc_bp.route("/tc-operacao")
@login_required
def tc_multi():
    u = current_user()
    if u["role"] != "admin":
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))
    tcs = list_tcs()
    return render_template("tc_multi.html", tcs=tcs)


@tc_bp.route("/tc-wall")
@login_required
def tc_wall():
    """
    Tela de acompanhamento em painel grande, exibindo duas TCs (esquerda/direita)
    com fundo verde/vermelho conforme estado de operação.
    """
    u = current_user()
    all_cts = list_tcs()
    if u["role"] in ("admin", "supervisor"):
        allowed = all_cts
    else:
        try:
            ids = set(list_user_tc_ids(u["id"]))
            allowed = [ct for ct in all_cts if ct["id"] in ids]
        except Exception:
            allowed = []
    return render_template("tc_wall.html", cts=allowed)


@tc_bp.route("/tc-wall-config", methods=["GET", "POST"])
@login_required
def tc_wall_config():
    """
    Tela de configuração das telas do painel grande (apenas admin/supervisor).
    Permite criar/remover telas, definindo qual TC aparece em cada lado.
    """
    u = current_user()
    if u["role"] != "admin":
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "delete":
            layout_id_raw = request.form.get("layout_id")
            try:
                layout_id = int(layout_id_raw)
                delete_layout(layout_id)
                flash("Tela removida.", "info")
            except Exception:
                flash("Falha ao remover tela.", "error")
            return redirect(url_for("tc.tc_wall_config"))

        # Criação simples de nova tela
        name = (request.form.get("name") or "").strip()
        left_raw = (request.form.get("left_tc_id") or "").strip()
        right_raw = (request.form.get("right_tc_id") or "").strip()

        if not name:
            flash("Nome da tela é obrigatório.", "error")
            return redirect(url_for("tc.tc_wall_config"))

        left_tc_id = None
        right_tc_id = None
        try:
            if left_raw:
                left_tc_id = int(left_raw)
        except Exception:
            flash("TC da esquerda inválida.", "error")
            return redirect(url_for("tc.tc_wall_config"))
        try:
            if right_raw:
                right_tc_id = int(right_raw)
        except Exception:
            flash("TC da direita inválida.", "error")
            return redirect(url_for("tc.tc_wall_config"))

        if left_tc_id is None and right_tc_id is None:
            flash("Selecione ao menos uma TC (esquerda ou direita).", "error")
            return redirect(url_for("tc.tc_wall_config"))

        try:
            new_id = create_layout(name=name, left_tc_id=left_tc_id, right_tc_id=right_tc_id)
            flash("Tela criada com sucesso.", "success")
            return redirect(url_for("tc.tc_wall_screen", layout_id=new_id))
        except Exception:
            flash("Falha ao criar tela (nome duplicado ou erro de banco).", "error")
            return redirect(url_for("tc.tc_wall_config"))

    all_cts = list_tcs()
    layouts = list_layouts()
    return render_template("tc_wall_config.html", tcs=all_cts, layouts=layouts)


@tc_bp.route("/tc-wall-view")
@login_required
def tc_wall_view_default():
    """
    Lista de painéis disponíveis para o usuário atual.
    - Admin vê todos os painéis.
    - Operador vê apenas painéis que contenham ao menos uma TC à qual ele tem acesso.
    - Viewer não tem acesso.
    """
    u = current_user()
    if u["role"] == "viewer":
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))

    layouts = list_layouts()
    if not layouts:
        flash("Nenhum painel configurado.", "error")
        return redirect(url_for("index"))

    # Admin vê todos; operadores só veem paineis com TCs às quais têm acesso
    if u["role"] in ("admin", "supervisor"):
        allowed = layouts
    else:
        allowed = []
        for lay in layouts:
            left_id = lay.get("left_tc_id")
            right_id = lay.get("right_tc_id")
            can_left = bool(left_id and user_can_view_tc(u, left_id))
            can_right = bool(right_id and user_can_view_tc(u, right_id))
            if can_left or can_right:
                allowed.append(lay)

    if not allowed:
        flash("Você não tem acesso a nenhum painel.", "error")
        return redirect(url_for("index"))

    return render_template("tc_wall_list.html", layouts=allowed)


@tc_bp.route("/tc-wall-edit/<int:layout_id>", methods=["GET", "POST"])
@login_required
def tc_wall_edit(layout_id: int):
    """
    Tela para editar uma configuração específica de painel grande.
    Permite trocar o nome e as TCs de cada lado.
    """
    u = current_user()
    if u["role"] not in ("admin", "supervisor"):
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))

    layout = get_layout(layout_id)
    if not layout:
        flash("Tela não encontrada.", "error")
        return redirect(url_for("tc.tc_wall_config"))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "update":
            name = (request.form.get("name") or "").strip()
            left_raw = (request.form.get("left_tc_id") or "").strip()
            right_raw = (request.form.get("right_tc_id") or "").strip()

            if not name:
                flash("Nome da tela é obrigatório.", "error")
                return redirect(url_for("tc.tc_wall_edit", layout_id=layout_id))

            left_tc_id = None
            right_tc_id = None
            try:
                if left_raw:
                    left_tc_id = int(left_raw)
            except Exception:
                flash("TC da esquerda inválida.", "error")
                return redirect(url_for("tc.tc_wall_edit", layout_id=layout_id))
            try:
                if right_raw:
                    right_tc_id = int(right_raw)
            except Exception:
                flash("TC da direita inválida.", "error")
                return redirect(url_for("tc.tc_wall_edit", layout_id=layout_id))

            if left_tc_id is None and right_tc_id is None:
                flash("Selecione ao menos uma TC (esquerda ou direita).", "error")
                return redirect(url_for("tc.tc_wall_edit", layout_id=layout_id))

            try:
                update_layout(layout_id=layout_id, name=name, left_tc_id=left_tc_id, right_tc_id=right_tc_id)
                flash("Tela atualizada com sucesso.", "success")
                return redirect(url_for("tc.tc_wall_config"))
            except Exception:
                flash("Falha ao atualizar tela.", "error")
                return redirect(url_for("tc.tc_wall_edit", layout_id=layout_id))

    all_cts = list_tcs()
    return render_template("tc_wall_edit.html", layout=layout, tcs=all_cts)


@tc_bp.route("/tc-wall-screen/<int:layout_id>")
@login_required
def tc_wall_screen(layout_id: int):
    """
    Tela de visualização do painel grande baseada em uma configuração salva.
    Não exibe controles de seleção; apenas as TCs definidas para cada lado.
    """
    layout = get_layout(layout_id)
    if not layout:
        return "Tela não encontrada.", 404
    return render_template("tc_wall_screen.html", layout=layout)

@tc_bp.route("/tc/<int:tc_id>/start", methods=["POST"])
@login_required
def tc_start(tc_id):
    user = current_user()
    log.info("Usuario %s (%s) solicitou START para TC %s", user.get("username"), user.get("role"), tc_id)
    if not user_can_control_tc(user, tc_id):
        flash("Voce nao tem permissao para iniciar esta TC.", "error")
        log.warning("START negado: usuario %s sem permissao para TC %s", user.get("username"), tc_id)
        return redirect(url_for("index"))

    tc_row = get_tc(tc_id)
    if not tc_row:
        flash("TC nao encontrada.", "error")
        log.error("START abortado: TC %s nao encontrada", tc_id)
        return redirect(url_for("index"))

    lote = request.form.get("lote")
    contagem_alvo_raw = request.form.get("contagem_alvo")
    contagem_alvo = None
    if contagem_alvo_raw is not None and contagem_alvo_raw.strip() != "":
        try:
            contagem_alvo = int(contagem_alvo_raw)
            if contagem_alvo <= 0:
                raise ValueError
        except Exception:
            flash("Contagem alvo deve ser um numero inteiro positivo.", "error")
            log.warning("START abortado: contagem alvo invalida (%s) para TC %s", contagem_alvo_raw, tc_id)
            return redirect(url_for("index"))
    source_type = request.form.get("source_type", "rtsp")
    file_path = (request.form.get("file_path") or "").strip() or None

    if not lote:
        flash("Lote e obrigatorio.", "error")
        log.warning("START abortado: lote vazio para TC %s", tc_id)
        return redirect(url_for("index"))

    # Se existir agente vinculado, aciona remoto em vez de rodar local
    host = None
    try:
        host = get_host_for_tc(tc_id)
    except Exception:
        host = None
    if host:
        base = f"http://{host}" if ":" in str(host) else f"http://{host}:9090"
        try:
            r = _agent_http.post(
                f"{base}/api/agent/v1/command/start",
                json={
                    "lote": lote,
                    "contagem_alvo": contagem_alvo,
                    "source_type": source_type,
                    "file_path": file_path,
                },
                timeout=15,
            )
            if request.headers.get("X-Requested-With") == "fetch":
                return ("", 204 if r.ok else 500)
            if r.ok:
                flash(f"{tc_row['name']} iniciada remotamente (agente).", "success")
            else:
                flash("Falha ao iniciar no agente remoto.", "error")
            return redirect(url_for("index"))
        except Exception as e:
            # Se falhar ao contatar, marque o agente como offline imediatamente
            try:
                from services.agent_repository import upsert_tc_status
                upsert_tc_status(tc_id, hostname=host, version=None, status="offline")
            except Exception:
                pass
            if request.headers.get("X-Requested-With") == "fetch":
                return ("", 500)
            flash(f"Falha ao contatar agente remoto: {e}", "error")
            return redirect(url_for("index"))

    cp = _ensure_cp(tc_row)

    if cp.session_active or cp.session_db_id is not None:
        if request.headers.get("X-Requested-With") == "fetch":
            log.info("START ignorado: sessao ja ativa para TC %s (via fetch)", tc_id)
            return ("", 204)
        flash("Ja existe uma sessao operando para esta TC.", "info")
        log.info("START ignorado: sessao ja ativa para TC %s", tc_id)
        return redirect(url_for("index"))

    active = None
    try:
        active = get_active_session_by_ct(tc_id)
    except Exception:
        active = None
    if active and active.get("status") in ("operando", "ativo"):
        if request.headers.get("X-Requested-With") == "fetch":
            return ("", 204)
        flash("Ja existe uma sessao operando registrada no banco para esta TC.", "info")
        return redirect(url_for("index"))

    log.info("START autorizado para TC %s (lote=%s, alvo=%s, source_type=%s, arquivo=%s)", tc_id, lote, contagem_alvo, source_type, file_path)
    cp.set_source(source_type, file_path)
    cp.start_session(lote, contagem_alvo)
    log.info("START executado para TC %s (sessao ativa=%s, lote=%s)", tc_id, cp.session_active, cp.session_lote)

    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)
    flash(f"{tc_row['name']} iniciada com lote {lote}.", "success")
    return redirect(url_for("index"))

# AJAX-friendly start endpoint used by OperaÃÂ§ÃÂµes TCs
@tc_bp.route("/tc/<int:tc_id>/start-ajax", methods=["POST"])
@login_required
def tc_start_ajax(tc_id):
    u = current_user()
    if not user_can_control_tc(u, tc_id):
        return ("VocÃÂª nÃÂ£o tem permissÃÂ£o para iniciar esta TC.", 403)

    tc_row = get_tc(tc_id)
    if not tc_row:
        return ("TC nÃÂ£o encontrada.", 404)

    lote = request.form.get("lote")
    contagem_alvo_raw = request.form.get("contagem_alvo")
    contagem_alvo = None
    if contagem_alvo_raw is not None and str(contagem_alvo_raw).strip() != "":
        try:
            contagem_alvo = int(contagem_alvo_raw)
            if contagem_alvo <= 0:
                raise ValueError
        except Exception:
            return ("Contagem alvo deve ser um nÃÂºmero inteiro positivo.", 400)

    source_type = request.form.get("source_type", "rtsp")
    file_path = (request.form.get("file_path") or "").strip() or None

    if not lote:
        return ("Lote ÃÂ© obrigatÃÂ³rio.", 400)

    host = None
    try:
        host = get_host_for_tc(tc_id)
    except Exception:
        host = None
    if host:
        base = f"http://{host}" if ":" in str(host) else f"http://{host}:9090"
        try:
            r = requests.post(
                f"{base}/api/agent/v1/command/start",
                json={
                    "lote": lote,
                    "contagem_alvo": contagem_alvo,
                    "source_type": source_type,
                    "file_path": file_path,
                },
                timeout=15,
            )
            return ("", 204 if r.ok else 500)
        except Exception:
            return ("", 500)

    cp = _ensure_cp(tc_row)
    if cp.session_active or cp.session_db_id is not None:
        return ("", 204)

    try:
        active = get_active_session_by_ct(tc_id)
    except Exception:
        active = None
    if active and active.get("status") in ("operando", "ativo"):
        return ("", 204)

    cp.set_source(source_type, file_path)
    cp.start_session(lote, contagem_alvo)
    return ("", 204)

@tc_bp.route("/tc/<int:tc_id>/stop", methods=["POST"])
@login_required
def tc_stop(tc_id):
    user = current_user()
    log.info("Usuario %s (%s) solicitou STOP para TC %s", user.get("username"), user.get("role"), tc_id)
    if not user_can_control_tc(user, tc_id):
        flash("Voce nao tem permissao para parar esta TC.", "error")
        log.warning("STOP negado: usuario %s sem permissao para TC %s", user.get("username"), tc_id)
        return redirect(url_for("index"))

    cp = tc_runtime.get(tc_id)
    if not cp:
        # quando remoto, pode não haver cp local
        tc_row = get_tc(tc_id)
        if not tc_row:
            flash("TC nao encontrada.", "error")
            log.error("STOP abortado: TC %s nao encontrada", tc_id)
            return redirect(url_for("index"))
        # cria sombra apenas para leitura de contagem/alvo se necessário
        from services.runtime import get_or_create_shadow
        cp = get_or_create_shadow(tc_id, name=tc_row.get("name"))

    observacao = (request.form.get("observacao") or "").strip()

    # Alvo da sessão (preferir runtime; fallback DB)
    try:
        alvo = cp.session_contagem_alvo
    except Exception:
        alvo = None

    # Quantidade atual: quando operando via agente remoto o shadow local
    # pode estar com 0. Fallback para o último total registrado no DB.
    qtd = int(getattr(cp, "current_session_count", 0) or 0)
    try:
        db_row = get_active_session_by_ct(tc_id)
        if db_row:
            if alvo is None:
                try:
                    alvo = db_row.get("contagem_alvo")
                except Exception:
                    pass
            if (qtd or 0) <= 0:
                try:
                    s_id = int(db_row.get("id"))
                    r = query_one(
                        "SELECT total_atual FROM session_log WHERE session_id = %s ORDER BY ts DESC LIMIT 1",
                        [s_id],
                    )
                    if r and r.get("total_atual") is not None:
                        qtd = int(r.get("total_atual"))
                except Exception:
                    pass
    except Exception:
        pass
    require_obs = alvo is not None and qtd != int(alvo)
    if require_obs and len(observacao) < 10:
        message = "Observacao (min. 10 caracteres) e obrigatoria quando total != contagem alvo."
        log.warning(
            "STOP negado: observacao insuficiente (qtd=%s alvo=%s) para TC %s",
            qtd,
            alvo,
            tc_id,
        )
        if request.headers.get("X-Requested-With") == "fetch":
            return (message, 400)
        flash(message, "error")
        return redirect(url_for("index"))
    if observacao and len(observacao) < 10:
        message = "Observacao deve ter pelo menos 10 caracteres."
        log.warning("STOP negado: observacao menor que 10 caracteres (TC %s)", tc_id)
        if request.headers.get("X-Requested-With") == "fetch":
            return (message, 400)
        flash(message, "error")
        return redirect(url_for("index"))

    # Verifica se há agente vinculado e envia STOP remoto
    host = None
    try:
        host = get_host_for_tc(tc_id)
    except Exception:
        host = None
    if host:
        base = f"http://{host}" if ":" in str(host) else f"http://{host}:9090"
        try:
            r = _agent_http.post(
                f"{base}/api/agent/v1/command/stop",
                json={"observacao": observacao or None},
                timeout=10,
            )
            if r.ok:
                # Atualiza sombra local imediatamente para limpar o dashboard
                try:
                    from services.runtime import get_or_create_shadow
                    cp_shadow = get_or_create_shadow(tc_id)
                    cp_shadow.session_active = False
                    cp_shadow.session_lote = None
                    cp_shadow.session_data = None
                    cp_shadow.session_hora_inicio = None
                    cp_shadow.session_contagem_alvo = None
                    cp_shadow.current_session_count = 0
                except Exception:
                    pass
            if request.headers.get("X-Requested-With") == "fetch":
                return ("", 204 if r.ok else 500)
            if r.ok:
                flash(f"{tc_id} parada remotamente.", "info")
            else:
                flash("Falha ao parar no agente remoto.", "error")
            return redirect(url_for("index"))
        except Exception as e:
            # Se falhar ao contatar, marque o agente como offline imediatamente
            try:
                from services.agent_repository import upsert_tc_status
                upsert_tc_status(tc_id, hostname=host, version=None, status="offline")
            except Exception:
                pass
            if request.headers.get("X-Requested-With") == "fetch":
                return ("", 500)
            flash(f"Falha ao contatar agente remoto: {e}", "error")
            return redirect(url_for("index"))

    log.info("STOP autorizado para TC %s (observacao=%s)", tc_id, bool(observacao))
    cp.stop_session(observacao=observacao or None)
    log.info("STOP executado para TC %s (total_final=%s)", tc_id, cp.current_session_count)
    # Limpa fan-out cache para liberar memória da TC
    try:
        _mjpeg_state.pop(tc_id, None)
    except Exception:
        pass

    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)
    flash(f"{cp.ct['name']} parada.", "info")
    return redirect(url_for("index"))

@tc_bp.route("/sse/tc/<int:tc_id>")
@login_required
def sse_tc(tc_id):
    u = current_user()
    if not user_can_view_tc(u, tc_id):
        return "forbidden", 403

    cp = tc_runtime.get(tc_id)
    if not cp:
        tc_row = get_tc(tc_id)
        if not tc_row:
            return "TC nÃÂ£o encontrada", 404
        cp = get_or_create_shadow(tc_id, name=tc_row.get("name"))

    def stream():
        while True:
            # Inclui status do banco para maior robustez (pÃÂ¡ginas que chegaram depois do START)
            try:
                db_row = get_active_session_by_ct(tc_id)
                db_status = (db_row.get("status") if db_row else None)
                db_total = db_row.get("total_final") if db_row else None
                # Total "ao vivo" (último total_atual do session_log)
                db_total_live = None
                if db_row and db_row.get("id") is not None:
                    now = time.time()
                    cached = _sse_db_cache.get(tc_id)
                    # aumenta TTL do cache para reduzir carga de consultas
                    if cached and cached.get('db_row') and cached['db_row'].get('id') == db_row.get('id') and (now - cached.get('ts', 0)) <= 2.0:
                        db_total_live = cached.get('db_total_live')
                    else:
                        row = query_one(
                            "SELECT total_atual FROM session_log WHERE session_id = %s ORDER BY ts DESC LIMIT 1",
                            [db_row["id"]],
                        )
                        if row and "total_atual" in row:
                            db_total_live = int(row["total_atual"]) if row["total_atual"] is not None else None
                        _sse_db_cache[tc_id] = {'ts': now, 'db_row': db_row, 'db_total_live': db_total_live}
            except Exception:
                db_status = None
                db_total = None
                db_total_live = None

            # Fallback para preencher campos a partir do banco quando o agente
            # iniciou remotamente e o shadow ainda não possui todos os dados.
            lote = cp.session_lote
            hora_ini = cp.session_hora_inicio
            cont_alvo = cp.session_contagem_alvo
            if (not lote or lote.strip() == "-") and db_row:
                try:
                    lote = (db_row.get("lote") or "").strip() or None
                except Exception:
                    pass
            if not hora_ini and db_row:
                try:
                    di = db_row.get("data_inicio")
                    if di:
                        from datetime import datetime
                        try:
                            hora_ini = di.strftime("%H:%M:%S") if hasattr(di, "strftime") else None
                        except Exception:
                            hora_ini = None
                except Exception:
                    pass
            if cont_alvo is None and db_row:
                try:
                    cont_alvo = db_row.get("contagem_alvo")
                except Exception:
                    pass

            # Define o count com regras:
            #  - Se operando (runtime ativo ou DB ativo), permita fallback ao "ao vivo" do log
            #  - Se parado, não use o "ao vivo"; mostre 0 ou o total_final do DB
            try:
                is_operando = bool(getattr(cp, "session_active", False)) or str(db_status or "").lower() in ("operando", "ativo")
            except Exception:
                is_operando = False

            count_val = int(cp.current_session_count)
            if is_operando:
                if (count_val is None or count_val == 0) and (db_total_live is not None):
                    try:
                        count_val = int(db_total_live)
                    except Exception:
                        pass
            else:
                # parado: exibe zero na UI (requisito atual). Não mostrar o total_final aqui.
                count_val = 0

            payload = {
                "session_active": cp.session_active,
                "lote": lote or "-",
                "data": cp.session_data,
                "hora_inicio": hora_ini or "-",
                "count": count_val,
                "fonte": cp.source_type,
                "contagem_alvo": cont_alvo,
                "db_status": db_status,
                "db_total_final": db_total,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            # Reduz frequência do SSE para aliviar threads/CPU do servidor
            time.sleep(2)

    return Response(stream(), mimetype="text/event-stream")

@tc_bp.route("/tc/<int:tc_id>/video")
@login_required
def tc_video(tc_id):
    # Importa OpenCV sob demanda para evitar dependência no Central quando só usa agente remoto
    try:
        import cv2  # type: ignore
    except Exception as _cv2_err:
        cv2 = None  # type: ignore
        _cv2_import_error = _cv2_err
    else:
        _cv2_import_error = None
    # Apenas admin pode abrir vÃÂ­deo individual
    u = current_user()
    if u["role"] != "admin":
        return "forbidden", 403

    cp = tc_runtime.get(tc_id)
    if not cp or not cp.session_active:
        return "Nenhuma sessÃÂ£o ativa para esta TC.", 404

    # Fan-out state por TC para reduzir encodes por viewer
    global _mjpeg_state
    def gen():
        # Se OpenCV não estiver disponível, interrompe o stream local com erro claro
        if _cv2_import_error is not None:
            yield (b'--frame\r\nContent-Type: text/plain\r\n\r\nOpenCV (cv2) nao instalado no servidor central.\r\n')
            return
        frame = None
        raw = None
        while True:
            # Encerra imediatamente o streaming quando a sessÃÂ£o parar
            if not cp.session_active:
                break
            try:
                frame = cp.last_vis_frame
                if frame is None and cp.camera is not None:
                    ret, raw = cp.camera.get_frame()
                    if not ret or raw is None:
                        time.sleep(0.02)
                        continue
                    frame = raw

                if frame is None:
                    time.sleep(0.02)
                    continue

                text = f"TOTAL: {int(cp.current_session_count)}"
                cv2.putText(frame, text, (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)

                state = _mjpeg_state.get(tc_id)
                if not state:
                    # Defaults por TC vindos do cadastro (quando existir)
                    tc_row = None
                    try:
                        tc_row = get_tc(tc_id)
                    except Exception:
                        tc_row = None
                    q = CENTRAL_MJPEG_QUALITY
                    f = CENTRAL_MJPEG_FPS
                    if tc_row is not None:
                        try:
                            if tc_row.get('stream_quality') is not None:
                                q = int(tc_row.get('stream_quality'))
                            if tc_row.get('stream_fps') is not None:
                                f = int(tc_row.get('stream_fps'))
                        except Exception:
                            pass
                    state = {'last_jpeg': None, 'last_ts': 0.0, 'quality': q, 'fps': f}
                    _mjpeg_state[tc_id] = state
                # Sobrepõe texto com configurações atuais
                try:
                    overlay = f"FPS:{int(state.get('fps', CENTRAL_MJPEG_FPS))} Q:{int(state.get('quality', CENTRAL_MJPEG_QUALITY))}"
                    cv2.putText(frame, overlay, (15, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 255, 180), 2)
                except Exception:
                    pass

                fps = float(state.get('fps', CENTRAL_MJPEG_FPS))
                interval = max(0.01, 1.0 / fps)
                now = time.time()
                if state['last_jpeg'] is None or (now - state['last_ts']) >= interval:
                    ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(state.get('quality', CENTRAL_MJPEG_QUALITY))])
                    if ok:
                        state['last_jpeg'] = buffer.tobytes()
                        state['last_ts'] = now
                data = state['last_jpeg']
                if data:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + data + b'\r\n')
                time.sleep(0.01)
            except Exception as e:
                if frame is not None:
                    cv2.putText(frame, f"ERRO: {e}", (15, 120),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    ok, buffer = cv2.imencode('.jpg', frame)
                    if ok:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.1)

    # Stream MJPEG com boundary 'frame'
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


@tc_bp.route("/tc/<int:tc_id>/video_proxy")
@login_required
def tc_video_proxy(tc_id):
    # Proxy inteligente: se a TC estiver operando via agente remoto,
    # redireciona/propaga o stream do agente; caso contrário, usa o stream local.
    cp = tc_runtime.get(tc_id)
    if not cp:
        tc_row = get_tc(tc_id)
        if not tc_row:
            return "TC não encontrada", 404
        cp = get_or_create_shadow(tc_id, name=tc_row.get("name"))

    if getattr(cp, "source_type", "") == "agent-remote":
        host = None
        try:
            host = get_host_for_tc(tc_id)
        except Exception:
            host = None
        if not host:
            return "Agente offline ou não identificado.", 503
        base = f"http://{host}" if ":" in str(host) else f"http://{host}:9090"
        url = f"{base}/api/agent/v1/video"
        try:
            r = _agent_http.get(url, stream=True, timeout=(3, 30))
        except Exception as e:
            return f"Falha ao conectar ao agente: {e}", 502
        if not r.ok:
            return f"Agente respondeu HTTP {r.status_code}", 502
        def proxy():
            try:
                for chunk in r.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    yield chunk
            finally:
                try:
                    r.close()
                except Exception:
                    pass
        return Response(proxy(), mimetype='multipart/x-mixed-replace; boundary=frame')

    # local: reutiliza o handler padrão
    return tc_video(tc_id)


@tc_bp.route("/tc/<int:tc_id>/stream-settings", methods=["POST"])
@login_required
def tc_stream_settings(tc_id):
    """Ajusta FPS/qualidade do MJPEG local (não persiste; runtime apenas)."""
    u = current_user()
    if u.get("role") != "admin":
        return ("forbidden", 403)
    try:
        q = request.form.get("quality") or request.json.get("quality") if request.is_json else request.form.get("quality")
    except Exception:
        q = request.form.get("quality")
    try:
        f = request.form.get("fps") or request.json.get("fps") if request.is_json else request.form.get("fps")
    except Exception:
        f = request.form.get("fps")
    try:
        quality = int(q) if q is not None else None
    except Exception:
        quality = None
    try:
        fps = int(f) if f is not None else None
    except Exception:
        fps = None
    # Sane bounds
    if quality is not None:
        quality = max(30, min(95, quality))
    if fps is not None:
        fps = max(1, min(60, fps))

    st = _mjpeg_state.get(tc_id)
    if not st:
        st = {'last_jpeg': None, 'last_ts': 0.0, 'quality': CENTRAL_MJPEG_QUALITY, 'fps': CENTRAL_MJPEG_FPS}
        _mjpeg_state[tc_id] = st
    if quality is not None:
        st['quality'] = quality
    if fps is not None:
        st['fps'] = fps

    return ("", 204)

@tc_bp.route("/tc/<int:tc_id>/snapshot.jpg")
@login_required
def tc_snapshot(tc_id):
    """Retorna um snapshot JPEG atual para calibração (resolução real)."""
    cp = tc_runtime.get(tc_id)
    # Se origem é agente remoto, proxy o snapshot do agente
    if cp and getattr(cp, "source_type", "") == "agent-remote":
        host = None
        try:
            host = get_host_for_tc(tc_id)
        except Exception:
            host = None
        if not host:
            return ("Agente offline", 503)
        try:
            base = f"http://{host}" if ":" in str(host) else f"http://{host}:9090"
            r = _agent_http.get(f"{base}/api/agent/v1/snapshot.jpg", timeout=5)
        except Exception as e:
            return (f"Falha ao obter snapshot do agente: {e}", 502)
        if not r.ok:
            return (f"Agente respondeu HTTP {r.status_code}", 502)
        return Response(r.content, mimetype='image/jpeg')

    # Local: usa last_vis_frame ou captura um frame da câmera
    import cv2
    if not cp:
        tc_row = get_tc(tc_id)
        if not tc_row:
            return ("TC não encontrada", 404)
        cp = get_or_create_shadow(tc_id, name=tc_row.get("name"))
    frame = getattr(cp, "last_vis_frame", None)
    if frame is None and getattr(cp, "camera", None) is not None:
        try:
            ret, raw = cp.camera.get_frame()
            if ret:
                frame = raw
        except Exception:
            frame = None
    if frame is None:
        return ("no frame", 503)
    ok, buf = cv2.imencode('.jpg', frame)
    if not ok:
        return ("encode error", 500)
    return Response(buf.tobytes(), mimetype='image/jpeg')
