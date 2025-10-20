# routes/agent.py
import logging
from flask import Blueprint, request, jsonify, g
from services.session_repository import create_session, insert_log, finish_session
from services.tc_repository import get_tc
from services.runtime import get_or_create_shadow
from services.agent_repository import get_agent_by_token, upsert_status, upsert_tc_status
from datetime import datetime

logs = logging.getLogger(__name__)

agent_bp = Blueprint("agent", __name__, url_prefix="/api/agent/v1")


def _require_token():
    """Valida Bearer token e retorna o registro do agente (ou None)."""
    auth = request.headers.get('Authorization', '')
    if not auth.lower().startswith('bearer '):
        return None
    token = auth.split(' ', 1)[1].strip()
    if not token:
        return None
    agent = get_agent_by_token(token)
    if agent and agent.get('active'):
        g.current_agent = agent
        return agent
    return None


@agent_bp.route("/config/<int:tc_id>", methods=["GET"])
def get_config(tc_id: int):
    """Retorna a configuração de captura da TC no banco para o agente.
    Modo permissivo (sem token) por enquanto.
    """
    try:
        tc = get_tc(tc_id)
        if not tc:
            return jsonify({"error": "tc_not_found"}), 404
        cfg = {
            "id": tc["id"],
            "name": tc["name"],
            "path": tc.get("source_path"),
            "roi": tc.get("roi"),
            "model": tc.get("model_path") or "sacaria_yolov5n.pt",
            "line_offset_red": tc.get("line_offset_red", 40),
            "line_offset_blue": tc.get("line_offset_blue", -40),
            "flow_mode": tc.get("flow_mode") or "cima",
            "max_lost": int(tc.get("max_lost", 2) or 0),
            "match_dist": float(tc.get("match_dist", 150) or 150),
            "min_conf": float(tc.get("min_conf", 0.8) or 0.8),
            "missed_frame_dir": (tc.get("missed_frame_dir") or "").strip(),
        }
        # tipo de fonte é sempre RTSP no modelo atual
        cfg["source_type"] = "rtsp"
        # parâmetros opcionais de streaming (Central dita defaults para o Agent)
        try:
            if tc.get("stream_fps") is not None:
                cfg["stream_fps"] = int(tc.get("stream_fps"))
            if tc.get("stream_quality") is not None:
                cfg["stream_quality"] = int(tc.get("stream_quality"))
        except Exception:
            pass
        return jsonify(cfg), 200
    except Exception as e:
        logs.exception("[AGENT] get_config failed: %s", e)
        return jsonify({"error": "server_error"}), 500


@agent_bp.route("/heartbeat", methods=["POST"])
def heartbeat():
    agent = _require_token()  # opcional por enquanto
    payload = request.get_json(silent=True) or {}
    logs.info("[AGENT] heartbeat: %s", payload)
    try:
        if agent:
            upsert_status(
                agent_db_id=agent['id'],
                tc_id=payload.get('tc_id'),
                hostname=payload.get('hostname'),
                version=payload.get('version'),
                status=payload.get('status'),
            )
        else:
            tc_id = payload.get('tc_id')
            if tc_id is not None:
                upsert_tc_status(
                    tc_id=int(tc_id),
                    hostname=payload.get('hostname'),
                    version=payload.get('version'),
                    status=payload.get('status'),
                )
    except Exception as e:
        logs.warning("[AGENT] heartbeat persist error: %s", e)
    return jsonify({"ok": True}), 200


@agent_bp.route("/session/start", methods=["POST"])
def session_start():
    agent = _require_token()  # opcional
    payload = request.get_json(silent=True) or {}
    logs.info("[AGENT] session_start: %s", payload)
    # Esperado: { agent_id, tc_id, lote, contagem_alvo, started_at }
    tc_id = int(payload.get("tc_id")) if payload.get("tc_id") is not None else None
    lote = (payload.get("lote") or "").strip()
    contagem_alvo = payload.get("contagem_alvo")
    if tc_id is None or not lote:
        return jsonify({"error": "missing tc_id or lote"}), 400
    if agent:
        agent_tc = agent.get('tc_id')
        if agent_tc is not None and tc_id != int(agent_tc):
            return jsonify({"error": "forbidden_tc"}), 403
    try:
        session_db_id = create_session(tc_id, lote, contagem_alvo)
    except Exception as e:
        logs.exception("[AGENT] fail to create session: %s", e)
        return jsonify({"error": "db_error"}), 500

    # Atualiza espelho em memória para SSE
    tc_row = None
    try:
        tc_row = get_tc(tc_id)
    except Exception:
        tc_row = None
    name = tc_row.get("name") if tc_row else None
    shadow = get_or_create_shadow(tc_id, name=name)
    shadow.session_active = True
    shadow.session_lote = lote
    shadow.session_contagem_alvo = contagem_alvo
    shadow.session_db_id = session_db_id
    shadow.current_session_count = 0
    try:
        now = datetime.now()
        shadow.session_data = now.strftime("%d/%m/%Y")
        shadow.session_hora_inicio = now.strftime("%H:%M:%S")
    except Exception:
        pass

    return jsonify({"ok": True, "session_db_id": session_db_id}), 200


@agent_bp.route("/session/update", methods=["POST"])
def session_update():
    agent = _require_token()  # opcional
    payload = request.get_json(silent=True) or {}
    logs.info("[AGENT] session_update: %s", payload)
    # Esperado: { agent_id, tc_id, session_db_id, total, increment, ts, log }
    tc_id = int(payload.get("tc_id")) if payload.get("tc_id") is not None else None
    session_id = payload.get("session_db_id")
    total = payload.get("total")
    observacao = payload.get("observacao")
    delta = payload.get("increment")
    if tc_id is None or session_id is None:
        return jsonify({"error": "missing tc_id or session_db_id"}), 400
    if agent:
        agent_tc = agent.get('tc_id')
        if agent_tc is not None and tc_id != int(agent_tc):
            return jsonify({"error": "forbidden_tc"}), 403
    # Valida no banco se a sessão ainda está ativa para aceitar o update
    try:
        from services.db import query_one
        s = query_one("SELECT status FROM session WHERE id = %s", [int(session_id)])
        status_val = (s.get("status") or "").strip().lower() if s else None
        is_active = status_val in ("operando", "ativo")
    except Exception:
        # Em caso de falha de consulta, assuma não ativo para evitar contagem fantasma
        is_active = False

    shadow = get_or_create_shadow(tc_id)

    if not is_active:
        # Sessão já finalizada no servidor: ignore o update e marque sombra como parada
        try:
            shadow.session_active = False
            shadow.session_db_id = None
        except Exception:
            pass
        return jsonify({"ok": True, "ignored": True, "reason": "session_finished"}), 200

    # Persistir log quando houver total/delta (somente para sessões ativas)
    try:
        if total is not None and delta is not None:
            insert_log(int(session_id), int(tc_id), int(delta), int(total))
    except Exception as e:
        logs.exception("[AGENT] fail to insert log: %s", e)
        return jsonify({"error": "db_error"}), 500

    # Atualiza espelho para SSE
    if total is not None:
        try:
            shadow.current_session_count = int(total)
        except Exception:
            pass
    shadow.session_active = True

    return jsonify({"ok": True}), 200


@agent_bp.route("/session/finish", methods=["POST"])
def session_finish():
    agent = _require_token()  # opcional
    payload = request.get_json(silent=True) or {}
    logs.info("[AGENT] session_finish: %s", payload)
    # Esperado: { agent_id, tc_id, session_db_id, finished_at, total }
    tc_id = int(payload.get("tc_id")) if payload.get("tc_id") is not None else None
    session_id = payload.get("session_db_id")
    total = payload.get("total")
    observacao = payload.get("observacao")
    if tc_id is None or session_id is None:
        return jsonify({"error": "missing tc_id or session_db_id"}), 400
    if agent:
        agent_tc = agent.get('tc_id')
        if agent_tc is not None and tc_id != int(agent_tc):
            return jsonify({"error": "forbidden_tc"}), 403
    try:
        finish_session(int(session_id), int(total) if total is not None else 0, status='finalizado', observacao=observacao)
    except Exception as e:
        logs.exception("[AGENT] fail to finish session: %s", e)
        return jsonify({"error": "db_error"}), 500

    # Atualiza espelho para SSE
    shadow = get_or_create_shadow(tc_id)
    if total is not None:
        try:
            shadow.current_session_count = int(total)
        except Exception:
            pass
    shadow.session_active = False
    shadow.session_db_id = None

    return jsonify({"ok": True}), 200
