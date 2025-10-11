import cv2

import torch

from datetime import datetime

import os

import numpy as np

import sys

import logging

# Supressao de avisos do PyTorch/YOLO

import warnings

warnings.filterwarnings('ignore')

log = logging.getLogger(__name__)

class IndustrialTagDetector:

    def __init__(self, model_path='sacaria_yolov5n.pt', roi=(0, 0, 0, 0), log_file=None, match_dist=150,

                 cross_point_mode: str = 'meio', line_offset_red: int = 40, line_offset_blue: int = -40,

                 flow_mode: str = 'cima', max_lost: int = 2, min_conf: float = 0.8,

                 missed_frame_dir: str | None = None, ct_id: int | None = None, ct_name: str | None = None):

        # 1. Configuraaes do Modelo e Ambiente (uso local do YOLOv5)

        self.model_path_requested = model_path

        self.model_path_resolved = None

        self.model_path_exists = None

        self.model_path_for_load = model_path

        try:

            self.line_offset_red = int(line_offset_red)

        except Exception:

            self.line_offset_red = 40

        try:

            self.line_offset_blue = int(line_offset_blue)

        except Exception:

            self.line_offset_blue = -40

        flow_mode_norm = (flow_mode or "cima").strip().lower()

        if flow_mode_norm not in ("cima", "baixo", "sem_fluxo"):

            flow_mode_norm = "cima"

        self.flow_mode = flow_mode_norm

        try:

            self.max_lost = max(0, int(max_lost))

        except Exception:

            self.max_lost = 2

        try:

            self.match_dist = float(match_dist)

        except Exception:

            self.match_dist = 150.0

        if self.match_dist <= 0:

            self.match_dist = 150.0

        try:

            self.min_conf = float(min_conf)

        except Exception:

            self.min_conf = 0.8

        if self.min_conf < 0:

            self.min_conf = 0.0

        if self.min_conf > 1:

            self.min_conf = 1.0

        self.missed_frame_dir = (missed_frame_dir or "").strip() or None

        self.ct_id = ct_id

        self.ct_name = ct_name

        self.current_session_lote = None
        self.current_session_dir = None

        if self.missed_frame_dir:

            try:

                os.makedirs(self.missed_frame_dir, exist_ok=True)

            except Exception as err:

                log.warning("Nao foi possivel criar diretorio de imagens nao contadas (%s): %s", self.missed_frame_dir, err)

                self.missed_frame_dir = None

        try:

            # Evita qualquer tentativa de auto-instalaao de dependaancias pelo YOLOv5

            os.environ.setdefault('YOLOV5_NO_AUTOINSTALL', '1')

            # Caminho local para o repositario YOLOv5 dentro do projeto

            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

            yolo_dir = os.path.join(project_root, 'third_party', 'yolov5')

            if not os.path.isdir(yolo_dir):

                raise FileNotFoundError(f"Diretorio YOLOv5 nao encontrado: {yolo_dir}")

            model_candidate = model_path or ""

            search_candidates = []

            if model_candidate:

                if os.path.isabs(model_candidate):

                    search_candidates.append(model_candidate)

                else:

                    search_candidates.append(os.path.join(project_root, model_candidate))

                    search_candidates.append(os.path.abspath(model_candidate))

                    search_candidates.append(model_candidate)

            else:

                search_candidates.append(model_candidate)

            resolved_path = None

            for candidate in search_candidates:

                if candidate and os.path.isfile(candidate):

                    resolved_path = candidate

                    break

            if resolved_path is None:

                resolved_path = search_candidates[0] if search_candidates else model_candidate

            if resolved_path:

                resolved_path = os.path.normpath(resolved_path)

            exists = bool(resolved_path and os.path.isfile(resolved_path))

            self.model_path_resolved = resolved_path

            self.model_path_exists = exists

            self.model_path_for_load = resolved_path if exists else (model_path or resolved_path or "")

            log.info(

                "[Detector] Modelo solicitado='%s' | resolvido='%s' | encontrado=%s",

                model_path,

                self.model_path_resolved,

                "sim" if exists else "nao"

            )

            if not exists:

                log.warning(

                    "[Detector] Arquivo de modelo nao localizado. Sera utilizada a referencia '%s'.",

                    self.model_path_for_load

                )

            # Carrega o modelo a partir do repositario local

            # Requer que exista um 'hubconf.py' em yolo_dir (ja presente no repo oficial)

            self.model = torch.hub.load(

                yolo_dir,

                'custom',

                path=self.model_path_for_load,

                source='local',

                force_reload=False

            )

            self.model.eval()

            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'

        except Exception as e:

            log.error("Falha ao carregar o modelo YOLOv5 local: %s", e)

            self.model = None

        # 2. Configuraaes do Rastreador (Tracking)

        self.roi = roi

        self.counter = 0

        self.tracked_objects = {}

        self.next_id = 1

        # AJUSTES CRaTICOS DE ESTABILIDADE

        # Valores de max_lost e match_dist configuraveis via cadastro (ja atribuados no __init__)

        # MARGEM DE TOLERaNCIA (Histerese): 20 pixels para prevenir reset por jitter.

        self.reset_margin = 20

        # Linhas de Portao (Duplo Cruzamento)

        x_roi, y_roi, w_roi, h_roi = roi

        # Linha Vermelha (Portao SUPERIOR - Y menor): A 1/3 da altura do ROI

        if h_roi > 0:

            self.line_red_y = y_roi + int(h_roi / 3) + self.line_offset_red

            self.line_blue_y = y_roi + int(2 * h_roi / 3) + self.line_offset_blue

        else:

            self.line_red_y = 0

            self.line_blue_y = 0

        # Filtros: ID e Confianaa

        self.target_ids = [0]

        # Modo do ponto de cruzamento visual (inicio/meio/fim)

        m = (cross_point_mode or 'meio').strip().lower()

        if m not in ('inicio', 'meio', 'fim'):

            m = 'meio'

        self.cross_point_mode = m

        # Log

        self.log_file = log_file

    def _log(self, message):

        """Escreve a mensagem no arquivo de log temporario."""

        if self.log_file:

            try:

                with open(self.log_file, 'a', encoding='utf-8') as f:

                    f.write(message + '\n')

            except Exception as e:

                print(f"[ERRO LOG] Falha ao escrever no log: {e}")

    def set_session_context(self, lote: str | None):
        if lote is None:
            self.current_session_lote = None
            self.current_session_dir = None
            return
        if not self.missed_frame_dir:
            self.current_session_lote = None
            self.current_session_dir = None
            log.info("Snapshots desativados: nenhuma pasta configurada.")
            return
        lote_clean = (lote or "").strip()
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in lote_clean).strip("_")
        final_name = safe or "sem_lote"
        session_dir = os.path.join(self.missed_frame_dir, final_name)
        try:
            os.makedirs(session_dir, exist_ok=True)
        except PermissionError as err:
            log.error("Sem permissao para usar pasta de snapshots (%s): %s", session_dir, err)
            self.missed_frame_dir = None
            self.current_session_lote = None
            self.current_session_dir = None
            return
        except Exception as err:
            log.warning("Nao foi possivel garantir diretorio base de imagens (%s): %s", session_dir, err)
            self.current_session_lote = None
            self.current_session_dir = None
            return
        self.current_session_lote = final_name
        self.current_session_dir = session_dir
        log.info("Snapshots de nao contadas ativos em %s", session_dir)

    def _save_not_counted_snapshot(self, frame_with_box, obj, obj_id):
        if frame_with_box is None:
            log.warning("Snapshot nao salvo (frame vazio) para obj %s", obj_id)
            return
        target_dir = self.current_session_dir or self.missed_frame_dir
        if not target_dir:
            return
        if obj.get("snapshot_saved"):
            return
        try:
            os.makedirs(target_dir, exist_ok=True)
        except PermissionError as err:
            log.error("Sem permissao para gravar snapshots (%s): %s", target_dir, err)
            return
        except Exception as err:
            log.warning("Nao foi possivel garantir pasta de snapshots (%s): %s", target_dir, err)
            return
        try:
            x1 = int(obj.get("x1", obj.get("prev_x1", 0)))
            y1 = int(obj.get("y1", obj.get("prev_y1", 0)))
            x2 = int(obj.get("x2", obj.get("prev_x2", 0)))
            y2 = int(obj.get("y2", obj.get("prev_y2", 0)))
        except Exception as err:
            log.warning("Snapshot nao salvo (coordenadas invalidas) para obj %s: %s", obj_id, err)
            return
        h, w = frame_with_box.shape[:2]
        x1 = max(0, min(w, x1))
        x2 = max(0, min(w, x2))
        y1 = max(0, min(h, y1))
        y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            log.warning("Snapshot nao salvo (recorte invalido) para obj %s", obj_id)
            return
        frame_to_save = frame_with_box.copy()
        try:
            cv2.rectangle(frame_to_save, (x1, y1), (x2, y2), (255, 0, 0), 2)
        except Exception:
            pass
        lote_part = self.current_session_lote or "sem_lote"
        timestamp = datetime.now().strftime("%H%M%S")
        base_name = f"{lote_part}_{timestamp}_id{obj_id}"
        suffix = 0
        while True:
            name = base_name if suffix == 0 else f"{base_name}_{suffix}"
            path_file = os.path.join(target_dir, f"{name}.jpg")
            if not os.path.exists(path_file):
                break
            suffix += 1
        try:
            cv2.imwrite(path_file, frame_to_save)
            obj["snapshot_saved"] = True
            log.info("Snapshot nao contado salvo: %s", path_file)
        except PermissionError as err:
            log.error("Sem permissao para gravar snapshots (%s): %s", path_file, err)
        except Exception as err:
            log.warning("Falha ao salvar imagem de sacaria nao contada (%s): %s", path_file, err)

    def detect_and_tag(self, frame):

        """Executa a detecao, rastreamento, contagem e desenha no frame."""

        if self.model is None:

             return frame, 0

        raw_frame = frame.copy() if self.missed_frame_dir else frame

        results = self.model(frame, size=640)

        detections = results.pred[0].cpu().numpy()

        filtered_detections = []

        x_roi, y_roi, w_roi, h_roi = self.roi

        x_final, y_final = x_roi + w_roi, y_roi + h_roi

        h_frame, w_frame, _ = frame.shape

        is_roi_active = w_roi > 0 and h_roi > 0

        def crossed(prev: float, curr: float, line: float, direction: str) -> bool:

            if line is None:

                return False

            if direction == "up":

                return prev > line >= curr

            if direction == "down":

                return prev < line <= curr

            return False

        def reversed_cross(prev: float, curr: float, line: float, direction: str, margin: float) -> bool:

            if line is None:

                return False

            if direction == "up":

                return prev < line and curr >= line + margin

            if direction == "down":

                return prev > line and curr <= line - margin

            return False

        flow_mode = self.flow_mode

        if flow_mode == "cima":

            add_primary_line = self.line_blue_y

            add_primary_dir = "up"

            add_secondary_line = self.line_red_y

            add_secondary_dir = "up"

            sub_primary_line = self.line_red_y

            sub_primary_dir = "down"

            sub_secondary_line = self.line_blue_y

            sub_secondary_dir = "down"

        elif flow_mode == "baixo":

            add_primary_line = self.line_red_y

            add_primary_dir = "down"

            add_secondary_line = self.line_blue_y

            add_secondary_dir = "down"

            sub_primary_line = self.line_blue_y

            sub_primary_dir = "up"

            sub_secondary_line = self.line_red_y

            sub_secondary_dir = "up"

        else:

            add_primary_line = add_secondary_line = sub_primary_line = sub_secondary_line = None

            add_primary_dir = add_secondary_dir = sub_primary_dir = sub_secondary_dir = None

        # 1. Desenha o ROI e Linhas (Para debug)

        if is_roi_active:

            cv2.rectangle(frame, (x_roi, y_roi), (x_final, y_final), (0, 255, 0), 2)

            cv2.line(frame,

                     (x_roi, self.line_red_y),

                     (x_final, self.line_red_y),

                     (0, 0, 0), 1) # Vermelho (INVISaVEL)

            cv2.line(frame,

                     (x_roi, self.line_blue_y),

                     (x_final, self.line_blue_y),

                     (0, 0, 0), 1) # Azul (INVISaVEL)

            # DEBUG: Mostra os valores das linhas

            cv2.putText(frame, f"Red Y: {self.line_red_y}", (x_final + 10, self.line_red_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

            cv2.putText(frame, f"Blue Y: {self.line_blue_y}", (x_final + 10, self.line_blue_y + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)

        else:

            crossing_line_x = int(w_frame * 0.50)

            cv2.line(frame, (crossing_line_x, 0), (crossing_line_x, h_frame), (0, 255, 255), 2)

        # 2. Filtra Detecaes (por Confianaa, Classe e ROI)

        for x1, y1, x2, y2, conf, cls_id in detections:

            if conf < self.min_conf or int(cls_id) not in self.target_ids:

                continue

            center_x = (x1 + x2) / 2

            center_y = (y1 + y2) / 2

            if is_roi_active:

                if not (x_roi <= center_x <= x_final and y_roi <= center_y <= y_final):

                    continue

            filtered_detections.append((x1, y1, x2, y2, conf, center_x, center_y))

        # 3. Rastreamento (Tracking)

        matched_ids = []

        for i, (dx1, dy1, dx2, dy2, dconf, dcx, dcy) in enumerate(filtered_detections):

            best_match_id = None

            min_dist = float('inf')

            for obj_id, obj_data in self.tracked_objects.items():

                dist = ((obj_data['cx'] - dcx)**2 + (obj_data['cy'] - dcy)**2)**0.5

                if dist < min_dist and dist < self.match_dist:

                    min_dist = dist

                    best_match_id = obj_id

            if best_match_id is not None and best_match_id not in matched_ids:

                # Atualizaao do Objeto Existente

                prev_cy = self.tracked_objects[best_match_id]['cy']

                prev_cx = self.tracked_objects[best_match_id]['cx']

                self.tracked_objects[best_match_id].update({

                    'x1': dx1, 'y1': dy1, 'x2': dx2, 'y2': dy2,

                    'cx': dcx, 'cy': dcy, 'prev_cy': prev_cy,

                    'lost_frames': 0,

                    'conf': dconf,

                    'prev_cx': prev_cx

                })

                matched_ids.append(best_match_id)

            elif best_match_id is None:

                # Inicializaao de um Novo Objeto

                self.tracked_objects[self.next_id] = {

                    'x1': dx1, 'y1': dy1, 'x2': dx2, 'y2': dy2,

                    'cx': dcx, 'cy': dcy,

                    'prev_cy': dcy,

                    'lost_frames': 0,

                    'counted': 0,     # 0 = Nao contado / -1 = Contagem Anulada

                    'direction': 0,   # 0 = Neutro, 1 = Esperando Vermelha (Subindo), -1 = Esperando Azul (Descendo)

                    'conf': dconf,

                    'prev_cx': dcx

                }

                self.next_id += 1

                matched_ids.append(self.next_id - 1)

        # 4. Atualiza Lost Frames, Conta e Desenha

        for obj_id in list(self.tracked_objects.keys()):

            obj = self.tracked_objects[obj_id]

            # Descarte rapido: se o centro sair do ROI
            if is_roi_active and (obj['cx'] < x_roi or obj['cx'] > x_final or obj['cy'] < y_roi or obj['cy'] > y_final):
                if obj.get('counted', 0) == 0:
                    self._save_not_counted_snapshot(frame.copy(), obj, obj_id)
                del self.tracked_objects[obj_id]
                continue

            if obj_id not in matched_ids:
                obj['lost_frames'] += 1
                if obj['lost_frames'] > self.max_lost:
                    if obj.get('counted', 0) == 0:
                        self._save_not_counted_snapshot(frame.copy(), obj, obj_id)
                    del self.tracked_objects[obj_id]
                    continue

            # Desenha a Bounding Box, ID e DEBUG!

            x1, y1, x2, y2 = map(int, [obj['x1'], obj['y1'], obj['x2'], obj['y2']])

            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)

            # DEBUG de Contagem e Status

            status_text = f"ID: {obj_id} Dir:{obj['direction']} Count:{obj['counted']}"

            cv2.putText(frame, status_text, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

            # Marca o ponto usado como referencia para a passagem nas linhas

            try:

                cx = int(obj['cx'])

                if self.cross_point_mode == 'inicio':

                    py = y1

                    label = 'I'

                elif self.cross_point_mode == 'fim':

                    py = y2

                    label = 'F'

                else:

                    py = int(obj['cy'])

                    label = 'M'

                cv2.circle(frame, (cx, int(py)), 4, (255, 0, 255), -1)

                cv2.putText(frame, label, (cx+6, int(py)+4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)

            except Exception:

                pass

            prev_cy = obj.get('prev_cy', obj['cy'])

            curr_cy = obj['cy']

            if flow_mode == "sem_fluxo":

                if obj['counted'] == 0:

                    self.counter += 1

                    obj['counted'] = 1

                    self._log(f"RECONHECIMENTO SEM FLUXO +1 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})")

            elif is_roi_active and add_primary_line is not None:

                if obj['direction'] == 0 and crossed(prev_cy, curr_cy, add_primary_line, add_primary_dir):

                    obj['direction'] = 1

                if (

                    obj['direction'] == 1

                    and obj['counted'] == 0

                    and crossed(prev_cy, curr_cy, add_secondary_line, add_secondary_dir)

                ):

                    self.counter += 1

                    obj['counted'] = 1

                    obj['direction'] = 0

                    self._log(

                        f"RECONHECIMENTO {flow_mode.upper()} +1 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})"

                    )

                if obj['direction'] == 0 and crossed(prev_cy, curr_cy, sub_primary_line, sub_primary_dir):

                    obj['direction'] = -1

                if obj['direction'] == -1 and crossed(prev_cy, curr_cy, sub_secondary_line, sub_secondary_dir):

                    if self.counter > 0:

                        self.counter -= 1

                        self._log(

                            f"RECONHECIMENTO {flow_mode.upper()} -1 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})"

                        )

                    else:

                        self._log(

                            f"CICLO {flow_mode.upper()} RETORNO (contador 0) {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})"

                        )

                    obj['counted'] = -1

                    obj['direction'] = 0

                if obj['direction'] == 1 and reversed_cross(prev_cy, curr_cy, add_primary_line, add_primary_dir, self.reset_margin):

                    obj['direction'] = 0

                    self._log(

                        f"CANCELAMENTO {flow_mode.upper()} (linha primaria, margem {self.reset_margin}) (ID: {obj_id})"

                    )

                if obj['direction'] == -1 and reversed_cross(prev_cy, curr_cy, sub_primary_line, sub_primary_dir, self.reset_margin):

                    obj['direction'] = 0

                    self._log(

                        f"CANCELAMENTO {flow_mode.upper()} (linha secundaria, margem {self.reset_margin}) (ID: {obj_id})"

                    )

            elif not is_roi_active and obj['counted'] == 0:

                crossing_line_x = int(w_frame * 0.50)

                if obj['cx'] >= crossing_line_x:

                    self.counter += 1

                    obj['counted'] = 1

                    self._log(

                        f"RECONHECIMENTO SEM ROI +1 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})"

                    )

            # CRaTICO: Atualiza prev_cy APENAS NO FINAL

            if obj_id in matched_ids:

                 obj['prev_cy'] = obj['cy']

        return frame, self.counter

    def get_current_count(self):

        return self.counter

