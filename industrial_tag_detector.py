import cv2
import torch
from datetime import datetime
import os
import numpy as np

# Supressão de avisos do PyTorch/YOLO
import warnings
warnings.filterwarnings('ignore')

class IndustrialTagDetector:
    def __init__(self, model_path='sacaria_yolov5n.pt', roi=(0, 0, 0, 0), log_file=None, match_dist=100):
        
        # 1. Configurações do Modelo e Ambiente
        try:
            # Força o carregamento do modelo no CPU, se não houver GPU
            self.model = torch.hub.load('ultralytics/yolov5', 'custom', path=model_path, force_reload=True)
            self.model.eval()
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        except Exception as e:
            print(f"[ERRO] Falha ao carregar o modelo YOLOv5: {e}")
            self.model = None

        # 2. Configurações do Rastreador (Tracking)
        self.roi = roi
        self.counter = 0
        self.tracked_objects = {}
        self.next_id = 1
        
        # AJUSTE DE ESTABILIDADE: Tolerância de 2 segundos de perda (24 frames)
        self.max_lost = 8 
        # AJUSTE DE SEPARAÇÃO: 150 pixels para SEPARAR sacarias que passam próximas.
        self.match_dist = 100 
        
        # Filtros: ID e Confiança
        self.target_ids = [0] # ID da sacaria (Cl_90asse 0)
        self.min_conf = 0.90 # Confiança mínima para detecção
        
        # Log
        self.log_file = log_file
        
    def _log(self, message):
        """Escreve a mensagem no arquivo de log temporário."""
        if self.log_file:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(message + '\n')
            except Exception as e:
                print(f"[ERRO LOG] Falha ao escrever no log: {e}")

    def detect_and_tag(self, frame):
        """Executa a detecção, rastreamento, contagem e desenha no frame."""
        if self.model is None:
             return frame, 0
             
        # Detecta usando o modelo (YOLOv5)
        results = self.model(frame, size=640) 
        detections = results.pred[0].cpu().numpy()

        filtered_detections = []
        x_roi, y_roi, w_roi, h_roi = self.roi
        x_final, y_final = x_roi + w_roi, y_roi + h_roi
        
        # Obtém a resolução real do frame
        h_frame, w_frame, _ = frame.shape
        
        # Definição crucial: verifica se o ROI foi definido (qualquer valor > 0)
        is_roi_active = w_roi > 0 and h_roi > 0

        # 1. Desenha o ROI (quadro verde) ou a Linha Central
        if is_roi_active:
            # Desenha o quadro verde (Zona de Contagem)
            cv2.rectangle(frame, (x_roi, y_roi), (x_final, y_final), (0, 255, 0), 2)
            
        else:
            # Desenha a linha amarela central (fallback se o ROI não estiver ativo)
            crossing_line_x = int(w_frame * 0.50)
            cv2.line(frame, (crossing_line_x, 0), (crossing_line_x, h_frame), (0, 255, 255), 2)

        # 2. Filtra Detecções por Confiança, Classe e ROI
        for x1, y1, x2, y2, conf, cls_id in detections:
            
            # Filtro por Confiança e Classe
            if conf < self.min_conf or int(cls_id) not in self.target_ids:
                continue

            # Calcula o centro da caixa detectada
            center_x = (x1 + x2) / 2
            center_y = (y1 + y2) / 2
            
            # Filtra por ROI (se o ROI estiver ativo e o centro estiver fora)
            if is_roi_active:
                if not (x_roi <= center_x <= x_final and y_roi <= center_y <= y_final):
                    continue

            filtered_detections.append((x1, y1, x2, y2, conf, center_x, center_y))

        # 3. Rastreamento
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
                
                # RESTAURADO: Rastreamento simplificado, o movimento Y não é mais rastreado explicitamente.
                prev_cx = self.tracked_objects[best_match_id]['cx'] 
                
                self.tracked_objects[best_match_id].update({
                    'x1': dx1, 'y1': dy1, 'x2': dx2, 'y2': dy2,
                    'cx': dcx, 'cy': dcy, 'prev_cx': prev_cx, # RESTAURADO
                    'lost_frames': 0,
                    'conf': dconf
                })
                matched_ids.append(best_match_id)
            
            elif best_match_id is None:
                self.tracked_objects[self.next_id] = {
                    'x1': dx1, 'y1': dy1, 'x2': dx2, 'y2': dy2,
                    'cx': dcx, 'cy': dcy, 'prev_cx': dcx, # RESTAURADO
                    'lost_frames': 0,
                    'counted': False,
                    'conf': dconf
                }
                self.next_id += 1
                matched_ids.append(self.next_id - 1)


        # 4. Atualiza Lost Frames, Conta e Desenha
        
        for obj_id in list(self.tracked_objects.keys()):
            obj = self.tracked_objects[obj_id]
            
            # === MANTIDO: DESCARTE CRÍTICO POR SAÍDA DE TELA (Evita Roubo de ID) ===
            # Deleta o ID imediatamente se o objeto sair dos limites da imagem (borda superior ou inferior)
            if obj['y1'] < 10 or obj['y2'] > h_frame - 10:
                 del self.tracked_objects[obj_id]
                 continue
            # ==========================================================

            
            if obj_id not in matched_ids:
                obj['lost_frames'] += 1
                
                if obj['lost_frames'] > self.max_lost:
                    del self.tracked_objects[obj_id]
                    continue
            else:
                 # RESTAURADO: Atualiza apenas a posição X anterior
                 obj['prev_cx'] = obj['cx'] 


            # Desenha a Bounding Box e ID
            x1, y1, x2, y2 = map(int, [obj['x1'], obj['y1'], obj['x2'], obj['y2']])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
            cv2.putText(frame, f"ID: {obj_id} Conf:{obj['conf']:.2f}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
            
            # ====================================================================
            # LÓGICA DE CONTAGEM ORIGINAL (Contagem por Zona de Contagem)
            # ====================================================================
            
            if not obj['counted']:
                
                if is_roi_active:
                    # Contagem com ROI: Se o centro estiver dentro do quadrado (Zona de Contagem)
                    if x_roi <= obj['cx'] <= x_final and y_roi <= obj['cy'] <= y_final:
                        self.counter += 1
                        obj['counted'] = True
                        self._log(f"RECONHECIMENTO {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} +1 (ID: {obj_id})")
                
                # Lógica de fallback para a linha central X, se o ROI não estiver ativo
                elif not is_roi_active:
                    crossing_line_x = int(w_frame * 0.50)
                    if obj['cx'] >= crossing_line_x:
                        self.counter += 1
                        obj['counted'] = True
                        self._log(f"RECONHECIMENTO {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} +1 (ID: {obj_id})")
                        
        return frame, self.counter

    def get_current_count(self):
        return self.counter