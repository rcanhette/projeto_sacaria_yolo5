import cv2
import threading
import time 

class VideoSource:
    def __init__(self, source_path):
        """Inicializa a fonte de vídeo (câmera ou arquivo) e o threading."""
        self.source_path = source_path
        
        # === CORREÇÕES PARA RTSP E BUFFER ===
        self.is_file = not source_path.lower().startswith("rtsp")
        
        if not self.is_file:
            # Tenta usar o backend FFMPEG (mais robusto para RTSP)
            self.cap = cv2.VideoCapture(source_path, cv2.CAP_FFMPEG) 
            
            # Reduz o buffer para pegar o frame mais recente e evitar atrasos/travamentos
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 3) 
        else:
            self.cap = cv2.VideoCapture(source_path)
        # ======================================

        if not self.cap.isOpened():
            print(f"[ERRO] Não foi possível abrir a fonte de vídeo: {source_path}")
            # Se não abrir, definimos como None para o loop não travar
            self.cap = None 
            return 
        
        self.frame = None
        self.ret = False
        self.lock = threading.Lock()
        
        # === LÓGICA DE SINCRONIZAÇÃO FPS ===
        self.delay = 0 
        
        if self.is_file:
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            
            if fps > 0:
                self.delay = 1 / fps 
                print(f"[FPS] Vídeo FPS: {fps:.2f}. Delay ajustado para: {self.delay:.4f}s")
            else:
                self.delay = 0.033 # Fallback
        # ==================================
        
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        """Método executado na thread separada para leitura contínua de frames."""
        # Se self.cap não foi aberto no __init__, a thread não precisa rodar
        if self.cap is None:
            return
            
        while not self.stop_event.is_set():
            
            # Tenta ler o frame
            ret, frame = self.cap.read()
            
            with self.lock:
                self.ret = ret
                if ret:
                    self.frame = frame
                else:
                    # Tratamento de falha (Se 'ret' for False)
                    if self.is_file:
                        # Se for um arquivo e chegar ao fim, reinicia
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        print("[INFO] Arquivo de vídeo reiniciado.")
                        continue
                        
                    # Para streams, se a leitura falhar, apenas continua após o sleep
                        
            # Aplicação do delay (essencial para estabilidade e CPU)
            if self.is_file and self.delay > 0:
                 time.sleep(self.delay)
            else:
                 # Sleep para liberar CPU (essencial para streams e evitar travamento)
                 time.sleep(0.001) 

    def get_frame(self):
        """Retorna o frame mais recente."""
        with self.lock:
            ret = self.ret
            # Garante que frame.copy() só é chamado se frame não for None
            frame = self.frame.copy() if self.frame is not None else None

        return ret, frame

    def release(self):
        """Para a thread e libera a captura do OpenCV."""
        self.stop_event.set()
        if self.cap:
             self.cap.release()
        if self.thread.is_alive():
             self.thread.join()