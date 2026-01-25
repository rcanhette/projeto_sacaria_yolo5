import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Iterable
from uuid import uuid4


class LocalQueue:
    """Fila local durável em SQLite para sessões e eventos quando o servidor
    central está indisponível.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self.last_sync_ms: int | None = None
        self.last_compact_ms: int | None = None

    def _connect(self):
        # check_same_thread=False para permitir uso em threads do agente
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS local_session (
                    local_id          INTEGER PRIMARY KEY,
                    tc_id             INTEGER NOT NULL,
                    lote              TEXT,
                    contagem_alvo     INTEGER,
                    created_at_ms     INTEGER NOT NULL,
                    status            TEXT DEFAULT 'operando',
                    observacao        TEXT,
                    finished_at_ms    INTEGER,
                    total_final       INTEGER,
                    sent_finish       INTEGER DEFAULT 0,
                    remote_session_id INTEGER
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS local_event (
                    event_id          TEXT PRIMARY KEY,
                    local_session_id  INTEGER NOT NULL,
                    delta             INTEGER NOT NULL,
                    total             INTEGER NOT NULL,
                    ts_ms             INTEGER NOT NULL,
                    sent              INTEGER DEFAULT 0,
                    FOREIGN KEY(local_session_id) REFERENCES local_session(local_id)
                )
                """
            )
            con.commit()

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _gen_local_id(self) -> int:
        # ID negativo baseado no epoch ms (reduz colisão entre execuções)
        v = -self._now_ms()
        return v

    # ---------------------------- Sessões ----------------------------
    def create_local_session(self, tc_id: int, lote: str, contagem_alvo: Optional[int]) -> int:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            local_id = self._gen_local_id()
            cur.execute(
                """
                INSERT INTO local_session(local_id, tc_id, lote, contagem_alvo, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (local_id, tc_id, lote, contagem_alvo, self._now_ms()),
            )
            con.commit()
            return local_id

    def set_remote_session(self, local_id: int, remote_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE local_session SET remote_session_id = ? WHERE local_id = ?",
                (remote_id, local_id),
            )
            con.commit()

    def ensure_shadow_session_remote(self, tc_id: int, lote: str, contagem_alvo: Optional[int], remote_id: int) -> int:
        """Cria uma sessão local de auditoria (mesmo estando online), já
        apontando para a sessão remota informada."""
        local_id = self.create_local_session(tc_id, lote, contagem_alvo)
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE local_session SET remote_session_id = ?, status='operando' WHERE local_id = ?",
                (int(remote_id), local_id),
            )
            con.commit()
        return local_id

    def enqueue_event(self, local_id: int, delta: int, total: int, ts_ms: Optional[int] = None, mark_sent: bool = False) -> str:
        ev_id = str(uuid4())
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO local_event(event_id, local_session_id, delta, total, ts_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ev_id, local_id, int(delta), int(total), ts_ms or self._now_ms()),
            )
            if mark_sent:
                con.execute("UPDATE local_event SET sent = 1 WHERE event_id = ?", (ev_id,))
            con.commit()
        return ev_id

    def mark_finish(self, local_id: int, total_final: int, observacao: Optional[str], status: str = "finalizado", mark_sent: bool = False) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                UPDATE local_session
                   SET status = ?,
                       total_final = ?,
                       observacao = COALESCE(?, observacao),
                       finished_at_ms = COALESCE(finished_at_ms, ?)
                 WHERE local_id = ?
                """,
                (status, int(total_final), observacao, self._now_ms(), local_id),
            )
            if mark_sent:
                con.execute("UPDATE local_session SET sent_finish = 1 WHERE local_id = ?", (local_id,))
            con.commit()

    # ----------------------------- Eventos ---------------------------
    def enqueue_event(self, local_id: int, delta: int, total: int, ts_ms: Optional[int] = None) -> str:
        ev_id = str(uuid4())
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO local_event(event_id, local_session_id, delta, total, ts_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ev_id, local_id, int(delta), int(total), ts_ms or self._now_ms()),
            )
            con.commit()
        return ev_id

    def _fetch_pending_sessions(self) -> list[tuple]:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT local_id, tc_id, lote, contagem_alvo, remote_session_id, status, total_final, observacao FROM local_session"
            )
            return cur.fetchall()

    def _fetch_pending_events(self, local_id: int) -> Iterable[tuple]:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                "SELECT event_id, delta, total FROM local_event WHERE local_session_id = ? AND sent = 0 ORDER BY ts_ms ASC",
                (local_id,),
            )
            return cur.fetchall()

    def _mark_event_sent(self, event_id: str) -> None:
        with self._lock, self._connect() as con:
            con.execute("UPDATE local_event SET sent = 1 WHERE event_id = ?", (event_id,))
            con.commit()

    def _mark_finish_sent(self, local_id: int) -> None:
        with self._lock, self._connect() as con:
            con.execute("UPDATE local_session SET sent_finish = 1 WHERE local_id = ?", (local_id,))
            con.commit()

    # ----------------------------- Métricas ---------------------------
    def count_pending(self) -> dict:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            ev = cur.execute("SELECT COUNT(*) FROM local_event WHERE sent = 0").fetchone()[0]
            # sessões sem remote id ou com finish não enviado
            sess_remote_missing = cur.execute("SELECT COUNT(*) FROM local_session WHERE remote_session_id IS NULL").fetchone()[0]
            sess_finish_pending = cur.execute(
                "SELECT COUNT(*) FROM local_session WHERE status NOT IN ('operando','ativo','pausado') AND sent_finish = 0"
            ).fetchone()[0]
        return {
            "events_pending": int(ev),
            "sessions_without_remote": int(sess_remote_missing),
            "sessions_finish_pending": int(sess_finish_pending),
        }

    def list_pending(self, events_limit: int = 50) -> dict:
        """Retorna resumo de pendências e uma amostra de eventos pendentes.
        events_limit: máximo de eventos pendentes a retornar (amostra ordenada por ts_ms).
        """
        events_limit = max(0, int(events_limit or 0))
        with self._lock, self._connect() as con:
            cur = con.cursor()
            # Sessões sem id remoto
            cur.execute(
                "SELECT local_id, tc_id, lote, contagem_alvo, created_at_ms FROM local_session WHERE remote_session_id IS NULL ORDER BY created_at_ms ASC"
            )
            sess_no_remote = [
                {
                    "local_id": r[0],
                    "tc_id": r[1],
                    "lote": r[2],
                    "contagem_alvo": r[3],
                    "created_at_ms": r[4],
                }
                for r in cur.fetchall()
            ]
            # Sessões finalizadas com finish pendente
            cur.execute(
                "SELECT local_id, tc_id, lote, total_final, finished_at_ms FROM local_session WHERE status NOT IN ('operando','ativo','pausado') AND sent_finish = 0 ORDER BY finished_at_ms ASC"
            )
            sess_finish = [
                {
                    "local_id": r[0],
                    "tc_id": r[1],
                    "lote": r[2],
                    "total_final": r[3],
                    "finished_at_ms": r[4],
                }
                for r in cur.fetchall()
            ]
            # Amostra de eventos pendentes
            sample = []
            if events_limit > 0:
                cur.execute(
                    "SELECT event_id, local_session_id, delta, total, ts_ms FROM local_event WHERE sent = 0 ORDER BY ts_ms ASC LIMIT ?",
                    (events_limit,),
                )
                sample = [
                    {
                        "event_id": r[0],
                        "local_session_id": r[1],
                        "delta": r[2],
                        "total": r[3],
                        "ts_ms": r[4],
                    }
                    for r in cur.fetchall()
                ]
        summary = self.count_pending()
        summary["sessions_without_remote_list"] = sess_no_remote
        summary["sessions_finish_pending_list"] = sess_finish
        summary["events_sample"] = sample
        return summary

    # ----------------------------- Manutenção ------------------------
    def compact(self, hard: bool = False) -> dict:
        """Remove eventos já enviados e sessões finalizadas marcadas como enviadas.
        Se hard=True, tenta rodar VACUUM (pode bloquear brevemente o arquivo).
        """
        deleted_events = 0
        deleted_sessions = 0
        with self._lock, self._connect() as con:
            cur = con.cursor()
            # Apaga eventos já enviados
            cur.execute("SELECT COUNT(*) FROM local_event WHERE sent = 1")
            deleted_events = int(cur.fetchone()[0] or 0)
            cur.execute("DELETE FROM local_event WHERE sent = 1")
            # Apaga sessões finalizadas com finish enviado e sem eventos pendentes
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM local_session s
                 WHERE s.sent_finish = 1
                   AND NOT EXISTS (
                        SELECT 1 FROM local_event e
                         WHERE e.local_session_id = s.local_id AND e.sent = 0
                   )
                """
            )
            deleted_sessions = int(cur.fetchone()[0] or 0)
            cur.execute(
                """
                DELETE FROM local_session
                 WHERE sent_finish = 1
                   AND NOT EXISTS (
                        SELECT 1 FROM local_event e
                         WHERE e.local_session_id = local_session.local_id AND e.sent = 0
                   )
                """
            )
            con.commit()

        if hard:
            # Tenta otimizar/compactar o arquivo
            try:
                con2 = sqlite3.connect(str(self.db_path))
                con2.isolation_level = None  # autocommit necessário para VACUUM
                con2.execute("VACUUM")
                con2.execute("PRAGMA optimize")
                con2.close()
            except Exception:
                pass
        else:
            try:
                with self._connect() as c3:
                    c3.execute("PRAGMA optimize")
            except Exception:
                pass

        remaining = self.count_pending()
        # Marca último compact
        try:
            self.last_compact_ms = self._now_ms()
        except Exception:
            pass
        return {
            "deleted_events": deleted_events,
            "deleted_sessions": deleted_sessions,
            "hard": bool(hard),
            "remaining": remaining,
            "last_compact_ms": self.last_compact_ms,
        }

    # ----------------------------- Sync ------------------------------
    def sync_to_central(self, central_client) -> None:
        """Tenta criar sessões remotas, reenviar eventos pendentes e finalizar sessões no servidor central.
        """
        sessions = self._fetch_pending_sessions()
        for local_id, tc_id, lote, contagem_alvo, remote_id, status, total_final, observacao in sessions:
            # 1) Garantir session remota
            if not remote_id:
                try:
                    remote_id = central_client.session_start(tc_id=tc_id, lote=lote or "", contagem_alvo=contagem_alvo)
                    if remote_id:
                        self.set_remote_session(local_id, int(remote_id))
                except Exception:
                    remote_id = None
            if not remote_id:
                # sem sessão remota não dá para enviar eventos
                continue

            # 2) Reenviar eventos pendentes
            for ev_id, delta, total in list(self._fetch_pending_events(local_id)):
                try:
                    central_client.session_update(tc_id=tc_id, session_db_id=int(remote_id), increment=int(delta), total=int(total), event_id=ev_id)
                    self._mark_event_sent(ev_id)
                except Exception:
                    break  # aguarda próxima rodada

            # 3) Finalizar sessão se necessário
            if status and status not in ('operando', 'ativo', 'pausado'):
                try:
                    # se já tem finish enviado, não envia novamente
                    # usamos sent_finish flag
                    with self._lock, self._connect() as con:
                        row = con.execute("SELECT sent_finish FROM local_session WHERE local_id = ?", (local_id,)).fetchone()
                        sent_finish = (row[0] == 1) if row else False
                    if not sent_finish:
                        central_client.session_finish(tc_id=tc_id, session_db_id=int(remote_id), total=int(total_final or 0), observacao=observacao, status=status)
                        self._mark_finish_sent(local_id)
                except Exception:
                    pass
        # Marca último sync
        try:
            self.last_sync_ms = self._now_ms()
        except Exception:
            pass
