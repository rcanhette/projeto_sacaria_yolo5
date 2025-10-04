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
            # Assumimos que 'ultralytics/yolov5' é o repositório correto.
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
        
        # AJUSTES CRÍTICOS DE ESTABILIDADE
        self.max_lost = 5   # Aumentado para 60 frames (2.0s em 30 FPS)
        self.match_dist = 200 # Aumentado para 180 pixels para sacos colados
        
        # MARGEM DE TOLERÂNCIA (Histerese): 20 pixels para prevenir reset por jitter.
        self.reset_margin = 18 
        
        # Linhas de Portão (Duplo Cruzamento)
        x_roi, y_roi, w_roi, h_roi = roi
        
        # Linha Vermelha (Portão SUPERIOR - Y menor): A 1/3 da altura do ROI
        self.line_red_y = y_roi + int(h_roi / 3) if h_roi > 0 else 0
        
        # Linha Azul (Portão INFERIOR - Y maior): A 2/3 da altura do ROI
        self.line_blue_y = y_roi + int(2 * h_roi / 3) if h_roi > 0 else 0
        
        # Filtros: ID e Confiança
        self.target_ids = [0] 
        self.min_conf = 0.90 
        
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
             
        results = self.model(frame, size=640) 
        detections = results.pred[0].cpu().numpy()

        filtered_detections = []
        x_roi, y_roi, w_roi, h_roi = self.roi
        x_final, y_final = x_roi + w_roi, y_roi + h_roi
        h_frame, w_frame, _ = frame.shape
        is_roi_active = w_roi > 0 and h_roi > 0
        
        # 1. Desenha o ROI e Linhas (Para debug)
        if is_roi_active:
            cv2.rectangle(frame, (x_roi, y_roi), (x_final, y_final), (0, 255, 0), 2)
            cv2.line(frame, 
                     (x_roi, self.line_red_y), 
                     (x_final, self.line_red_y), 
                     (0, 0, 0), 1) # Vermelho (INVISÍVEL)
            cv2.line(frame, 
                     (x_roi, self.line_blue_y), 
                     (x_final, self.line_blue_y), 
                     (0, 0, 0), 1) # Azul (INVISÍVEL)
            
            # DEBUG: Mostra os valores das linhas
            cv2.putText(frame, f"Red Y: {self.line_red_y}", (x_final + 10, self.line_red_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
            cv2.putText(frame, f"Blue Y: {self.line_blue_y}", (x_final + 10, self.line_blue_y + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)

            
        else:
            crossing_line_x = int(w_frame * 0.50)
            cv2.line(frame, (crossing_line_x, 0), (crossing_line_x, h_frame), (0, 255, 255), 2)

        # 2. Filtra Detecções (por Confiança, Classe e ROI)
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
                
                # Atualização do Objeto Existente
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
                
                # Inicialização de um Novo Objeto
                self.tracked_objects[self.next_id] = {
                    'x1': dx1, 'y1': dy1, 'x2': dx2, 'y2': dy2,
                    'cx': dcx, 'cy': dcy, 
                    'prev_cy': dcy, 
                    'lost_frames': 0,
                    'counted': 0,     # 0 = Não contado / -1 = Contagem Anulada
                    'direction': 0,   # 0 = Neutro, 1 = Esperando Vermelha (Subindo), -1 = Esperando Azul (Descendo)
                    'conf': dconf,
                    'prev_cx': dcx 
                }
                self.next_id += 1
                matched_ids.append(self.next_id - 1)


        # 4. Atualiza Lost Frames, Conta e Desenha
        
        for obj_id in list(self.tracked_objects.keys()):
            obj = self.tracked_objects[obj_id]
            
            # === DESCARTE RÁPIDO: Se o centro sair do ROI ===
            if is_roi_active and (obj['cx'] < x_roi or obj['cx'] > x_final or obj['cy'] < y_roi or obj['cy'] > y_final):
                 del self.tracked_objects[obj_id]
                 continue
            
            if obj_id not in matched_ids:
                obj['lost_frames'] += 1
                
                if obj['lost_frames'] > self.max_lost:
                    del self.tracked_objects[obj_id]
                    continue
            
            # Desenha a Bounding Box, ID e DEBUG!
            x1, y1, x2, y2 = map(int, [obj['x1'], obj['y1'], obj['x2'], obj['y2']])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
            
            # DEBUG de Contagem e Status
            status_text = f"ID: {obj_id} Dir:{obj['direction']} Count:{obj['counted']}"
            cv2.putText(frame, status_text, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
            
            
            # ====================================================================
            # LÓGICA DE DUPLO CRUZAMENTO (MÁQUINA DE ESTADOS)
            # ====================================================================
            
            if is_roi_active:
                prev_cy = obj.get('prev_cy', obj['cy'])
                curr_cy = obj['cy']
                
                # --- SENTIDO CIMA (CONTAR +1) ---
                
                # 1. Cruzou Linha AZUL (Portão INFERIOR) para cima: ENTRA NO ESTADO 1
                if prev_cy > self.line_blue_y and curr_cy <= self.line_blue_y and obj['direction'] == 0:
                    obj['direction'] = 1 # Estado: Esperando Vermelha (Sentido Cima)
                    
                # 2. Cruzou Linha VERMELHA (Portão SUPERIOR) para cima: CONTA +1
                if obj['direction'] == 1 and prev_cy > self.line_red_y and curr_cy <= self.line_red_y and obj['counted'] == 0:
                    self.counter += 1
                    obj['counted'] = 1 
                    obj['direction'] = 0 # Finalizou o ciclo
                    self._log(f"RECONHECIMENTO CIMA +1 (DUPLO CRUZAMENTO CONCLUÍDO) {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})")

                
                # --- SENTIDO DESCENDO (ANULAÇÃO -1) ---
                
                # 3. Cruzou Linha VERMELHA (Portão SUPERIOR) para baixo: ENTRA NO ESTADO -1
                if prev_cy < self.line_red_y and curr_cy >= self.line_red_y and obj['direction'] == 0:
                    obj['direction'] = -1 # Estado: Esperando Azul (Sentido Anulação)

                # 4. Cruzou Linha AZUL (Portão INFERIOR) para baixo: ANULA -1
                if obj['direction'] == -1 and prev_cy < self.line_blue_y and curr_cy >= self.line_blue_y:
                    
                    if self.counter > 0:
                        self.counter -= 1
                        obj['counted'] = -1 # Marca como anulado
                        self._log(f"RECONHECIMENTO BAIXO -1 (ANULAÇÃO DUPLA CONCLUÍDA/NOVO ID) {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} (ID: {obj_id})")
                    else:
                        obj['counted'] = -1
                        self._log(f"CICLO DE DESCIDA FINALIZADO (CONTADOR 0) (ID: {obj_id})")
                    
                    obj['direction'] = 0 # Limpar o estado


                
                # --- REGRAS DE CANCELAMENTO E RETORNO (COM MARGEM DE 20) ---
                
                # 5. RETORNO INCOMPLETO - Subindo (Cancela se voltar para BAIXO da Linha AZUL com margem)
                if obj['direction'] == 1 and prev_cy < self.line_blue_y and curr_cy >= self.line_blue_y + self.reset_margin:
                     obj['direction'] = 0 # Reseta a direção 
                     self._log(f"CANCELAMENTO CIMA (Recuou no Portão Azul com margem {self.reset_margin}) (ID: {obj_id})")

                # 6. RETORNO INCOMPLETO - Descendo (Cancela se voltar para CIMA da Linha VERMELHA com margem)
                if obj['direction'] == -1 and prev_cy > self.line_red_y and curr_cy <= self.line_red_y - self.reset_margin:
                     obj['direction'] = 0 # Reseta a direção
                     self._log(f"CANCELAMENTO BAIXO (Recuou no Portão Vermelho com margem {self.reset_margin}) (ID: {obj_id})")
                     
            
            # Fallback para contagem sem ROI
            elif not is_roi_active and obj['counted'] == 0:
                 crossing_line_x = int(w_frame * 0.50)
                 if obj['cx'] >= crossing_line_x:
                     self.counter += 1
                     obj['counted'] = 1
                     self._log(f"RECONHECIMENTO {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} +1 (ID: {obj_id})")
            
            # CRÍTICO: Atualiza prev_cy APENAS NO FINAL
            if obj_id in matched_ids:
                 obj['prev_cy'] = obj['cy'] 
                        
        return frame, self.counter

    def get_current_count(self):
        return self.counter