import cv2
import threading
import time 
import os

class VideoSource:
    def __init__(self, source_path):
        """Inicializa a fonte de vídeo (câmera ou arquivo) e o threading."""
        self.source_path = source_path
        self.cap = None
        self.frame = None
        self.ret = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        
        # === CORREÇÕES PARA RTSP E BUFFER ===
        self.is_file = not source_path.lower().startswith("rtsp")
        
        if not self.is_file:
            # Tenta usar o backend FFMPEG (mais robusto para RTSP)
            self.cap = cv2.VideoCapture(source_path, cv2.CAP_FFMPEG) 
            
            # Reduz o buffer para pegar o frame mais recente e evitar atrasos/travamentos
            try:
                buf_env = os.getenv('VIDEO_RTSP_BUFFER_SIZE')
                buffer_size = int(buf_env) if buf_env is not None else 3
            except Exception:
                buffer_size = 3
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)
        else:
            self.cap = cv2.VideoCapture(source_path)
        # ======================================

        if not self.cap.isOpened():
            print(f"[ERRO] Não foi possível abrir a fonte de vídeo: {source_path}")
            # Se não abrir, definimos como None para o loop não travar
            self.cap = None 
            return 

        # === LÓGICA DE SINCRONIZAÇÃO FPS ===
        self.delay = 0 
        
        if self.is_file:
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            
            # Configuração por ambiente: fator ou delay fixo em ms
            delay_ms_env = os.getenv('VIDEO_FILE_DELAY_MS')
            delay_factor_env = os.getenv('VIDEO_FILE_DELAY_FACTOR')

            if delay_ms_env is not None:
                try:
                    self.delay = max(0.0, float(delay_ms_env) / 1000.0)
                except Exception:
                    self.delay = 0.033
            elif fps > 0:
                try:
                    factor = float(delay_factor_env) if delay_factor_env is not None else 0.9
                except Exception:
                    factor = 0.9
                self.delay = max(0.0, factor / fps)
                print(f"[FPS] Vídeo FPS: {fps:.2f}. Delay ajustado para: {self.delay:.4f}s")
            else:
                # Fallback quando FPS não está disponível
                self.delay = 0.033
        # ==================================
        
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        """Método executado na thread separada para leitura contínua de frames."""
        # Se self.cap não foi aberto no __init__, a thread não precisa rodar
        if self.cap is None:
            return
            
        while not self.stop_event.is_set():
            
            # Tenta ler o frame
            try:
                ret, frame = self.cap.read()
            except Exception as e:
                # Proteção contra race condition: cap pode ser liberado durante read()
                # ou backend lançar exceção C++ (cv2.error). Encerra a thread com segurança.
                try:
                    # registra uma mensagem simples (evita quebrar se logger não existir)
                    print(f"[VideoSource] Exceção no read(): {e}. Encerrando thread de captura.")
                except Exception:
                    pass
                break
            
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
        # Ordem importa para evitar race com self.cap.read():
        # 1) sinaliza parada, 2) aguarda thread sair do loop, 3) libera cap
        try:
            self.stop_event.set()
        except Exception:
            pass
        try:
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass
