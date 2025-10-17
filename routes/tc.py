import time
import json
import cv2
import logging
import requests
from flask import Blueprint, render_template, Response, request, redirect, url_for, flash
# Lazy import de CapturePoint dentro de _ensure_cp para evitar carregar Torch
from services.tc_repository import get_tc, list_tcs
from services.session_repository import get_active_session_by_ct
from services.db import query_one
from services.agent_repository import get_host_for_tc
from services.runtime import tc_runtime, get_or_create_shadow
from routes.auth import current_user, login_required
from services.auth_repository import user_can_view_tc, user_can_control_tc
from services.session_repository import get_active_session_by_ct

tc_bp = Blueprint("tc", __name__)
log = logging.getLogger(__name__)

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
    return render_template("tc_detail.html", tc=tc_row, ct=tc_row, cp=cp)

@tc_bp.route("/tc-operacao")
@login_required
def tc_multi():
    u = current_user()
    if u["role"] != "admin":
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))
    tcs = list_tcs()
    return render_template("tc_multi.html", tcs=tcs)

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
        base = f"http://{host}:9090"
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
        base = f"http://{host}:9090"
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
    try:
        alvo = cp.session_contagem_alvo
    except Exception:
        alvo = None
    qtd = int(cp.current_session_count)
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
        base = f"http://{host}:9090"
        try:
            r = requests.post(
                f"{base}/api/agent/v1/command/stop",
                json={"observacao": observacao or None},
                timeout=10,
            )
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
                    row = query_one(
                        "SELECT total_atual FROM session_log WHERE session_id = %s ORDER BY ts DESC LIMIT 1",
                        [db_row["id"]],
                    )
                    if row and "total_atual" in row:
                        db_total_live = int(row["total_atual"]) if row["total_atual"] is not None else None
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
                # parado: prefira total_final do banco se existir; caso contrário, mantenha o runtime (geralmente 0)
                if db_total is not None:
                    try:
                        count_val = int(db_total)
                    except Exception:
                        pass

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
            time.sleep(1)

    return Response(stream(), mimetype="text/event-stream")

@tc_bp.route("/tc/<int:tc_id>/video")
@login_required
def tc_video(tc_id):
    # Apenas admin pode abrir vÃÂ­deo individual
    u = current_user()
    if u["role"] != "admin":
        return "forbidden", 403

    cp = tc_runtime.get(tc_id)
    if not cp or not cp.session_active:
        return "Nenhuma sessÃÂ£o ativa para esta TC.", 404

    def gen():
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

                ok, buffer = cv2.imencode('.jpg', frame)
                if ok:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
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
        url = f"http://{host}:9090/api/agent/v1/video"
        try:
            r = requests.get(url, stream=True, timeout=(3, 30))
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
            r = requests.get(f"http://{host}:9090/api/agent/v1/snapshot.jpg", timeout=5)
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
