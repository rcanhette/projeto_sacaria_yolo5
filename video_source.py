import cv2
import threading
import time 

class VideoSource:
    def __init__(self, source_path):
        """Inicializa a fonte de vídeo (câmera ou arquivo) e o threading."""
        self.source_path = source_path
        self.cap = cv2.VideoCapture(source_path)
        
        if not self.cap.isOpened():
            print(f"[ERRO] Não foi possível abrir a fonte de vídeo: {source_path}")
        
        self.frame = None
        self.ret = False
        self.lock = threading.Lock()
        
        # === LÓGICA DE SINCRONIZAÇÃO FPS (CORREÇÃO DE VÍDEO RÁPIDO) ===
        self.is_file = not source_path.lower().startswith("rtsp")
        self.delay = 0 
        
        if self.is_file:
            # Obtém o FPS original do arquivo
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            
            # Calcula o delay (tempo de espera entre frames)
            if fps > 0:
                self.delay = 1.0 / fps 
                print(f"[FPS] Vídeo FPS: {fps:.2f}. Delay ajustado para: {self.delay:.4f}s")
            else:
                self.delay = 0.033 
        # =============================================================
        
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        """Loop de leitura contínua do vídeo em uma thread separada."""
        while not self.stop_event.is_set():
            ret, frame = self.cap.read()
            
            with self.lock:
                self.ret = ret
                if ret:
                    self.frame = frame
                else:
                    # Se for um arquivo e chegar ao fim, reinicia o vídeo
                    if self.is_file:
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                        
            # === APLICAÇÃO DO DELAY NO LOOP DE LEITURA (MAIS EFICAZ) ===
            # Isso controla a velocidade em que os frames são lidos e armazenados
            if self.is_file and self.delay > 0:
                 time.sleep(self.delay)
            else:
                 # Pequeno sleep padrão para evitar uso excessivo de CPU em streams ao vivo
                 time.sleep(0.001) 
            # ==========================================================

    def get_frame(self):
        """Retorna o frame mais recente."""
        with self.lock:
            ret = self.ret
            frame = self.frame.copy() if self.frame is not None else None

        # O delay foi movido para o _run, não precisa de delay extra aqui
        return ret, frame

    def release(self):
        """Para a thread e libera a captura do OpenCV."""
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join()
        self.cap.release()