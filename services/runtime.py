# services/runtime.py
tc_runtime = {}  # { tc_id: CapturePoint | RemoteCaptureShadow }


class RemoteCaptureShadow:
    """
    Representa o estado mínimo de uma TC remota, suficiente para que
    os endpoints de SSE/MJPEG não quebrem e o dashboard atual funcione.
    Não captura vídeo nem roda detector localmente.
    """

    def __init__(self, tc_id: int, name: str | None = None):
        self.ct = {"id": tc_id, "name": name or f"TC{tc_id}"}
        # fonte/visual
        self.source_type = "agent-remote"
        self.last_vis_frame = None
        # contexto de sessão
        self.session_active = False
        self.session_lote = None
        self.session_data = None
        self.session_hora_inicio = None
        self.session_hora_fim = None
        self.session_db_id = None
        self.session_contagem_alvo = None
        # contagem
        self.current_session_count = 0
        self._last_session_logged_total = None
        self._base_counter_snapshot = 0

    # Mantém interface semelhante ao CapturePoint para chamadas existentes
    def release(self):
        self.session_active = False

def drop_tc_runtime(tc_id:int):
    cp = tc_runtime.pop(tc_id, None)
    if cp:
        try:
            cp.release()
        except Exception:
            pass


def get_or_create_shadow(tc_id: int, name: str | None = None) -> RemoteCaptureShadow:
    cp = tc_runtime.get(tc_id)
    if isinstance(cp, RemoteCaptureShadow):
        return cp
    # Se existir um CapturePoint real, reaproveita-o para não quebrar modo local
    if cp is not None:
        return cp
    shadow = RemoteCaptureShadow(tc_id, name=name)
    tc_runtime[tc_id] = shadow
    return shadow

# Backward aliases (temporary, to ease migration)
ct_runtime = tc_runtime
def drop_ct_runtime(ct_id:int):
    return drop_tc_runtime(ct_id)
