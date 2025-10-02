import os
import json
from flask import Flask, render_template, Response, request, redirect, url_for, flash, jsonify
import cv2
from datetime import datetime
from industrial_tag_detector import IndustrialTagDetector
import time
from threading import Lock

# IMPORTAÇÃO ASSUMIDA: A classe VideoSource que usa threading (recomendado)
from video_source import VideoSource 

# Filtro de Aviso
import warnings
warnings.filterwarnings(
    "ignore", 
    message="`torch.cuda.amp.autocast(args...)` is deprecated. Please use `torch.amp.autocast('cuda', args...)` instead."
)
# =========================================================

STATE_FILE = "contagem_state.json"
MODEL_PATH = "sacaria_yolov5n.pt" 

app = Flask(__name__)
app.secret_key = "super_secret_key"

# Variáveis globais para rastreamento
camera = None
detector = None
frame_lock = Lock()

# =========================================================
# Configurações de Câmera e ROI
# =========================================================

CONFIG_CAMERA_1 = {
    "name": "Câmera 1 (Produção)",
    "rtsp_url": "rtsp://admin:Coop%402020@172.16.10.83:554/Streaming/Channels/101", 
    "roi": (765, 495, 300, 375), 
    "model": "sacaria_yolov5n.pt"
}

CONFIG_CAMERA_2 = {
    "name": "Câmera 2 (Teste/Nova)",
    "rtsp_url": "rtsp://user:password@ip_da_sua_nova_camera:port/path", 
    "roi": (100, 100, 500, 400), 
    "model": "sacaria_yolov5n.pt" 
}

CAMERA_OPTIONS = {
    "CAM1": CONFIG_CAMERA_1,
    "CAM2": CONFIG_CAMERA_2,
}

# =========================================================
# Funções de Gerenciamento de Estado
# =========================================================

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            if 'camera_id' not in state:
                state['camera_id'] = "CAM1" 
            return state
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "status": "STOP",
            "lote": "",
            "data": "",
            "hora_inicio": "",
            "log_file": None,
            "source_path": None,
            "camera_id": "CAM1",
        }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def clear_state():
    state = load_state()
    state["status"] = "STOP"
    state["lote"] = ""
    state["data"] = ""
    state["hora_inicio"] = ""
    state["log_file"] = None
    state["source_path"] = None
    save_state(state)

def salvar_contagem(lote, data, hora_inicio, hora_fim, quantidade, log_file_path=None):
    try:
        if log_file_path:
            with open(log_file_path, 'a') as f:
                f.write(f"FIM: {hora_fim}\n")
                f.write(f"QUANTIDADE FINAL: {quantidade}\n")

        file_exists = os.path.exists("contagens_finalizadas.csv")
        with open("contagens_finalizadas.csv", 'a') as f:
            if not file_exists or os.path.getsize("contagens_finalizadas.csv") == 0:
                f.write("Lote,Data,Hora_Inicio,Hora_Fim,Quantidade\n")
            f.write(f"{lote},{data},{hora_inicio},{hora_fim},{quantidade}\n")
            
    except Exception as e:
        print(f"[ERRO DE LOG] Falha ao salvar a contagem final: {e}")

# =========================================================
# Rotas e Lógica de Vídeo
# =========================================================

# ROTA SSE (Server-Sent Events) - SOLUÇÃO FINAL PARA O CONTADOR HTML
@app.route('/sse_count')
def sse_count():
    def generate_count_events():
        global detector
        while True:
            # Polling no servidor a cada 1 segundo
            count = detector.counter if detector else 0
            
            # Formato SSE: data: <valor> \n\n
            data = f"data: {count}\n\n"
            yield data
            
            time.sleep(1) 

    return Response(generate_count_events(), mimetype='text/event-stream')


# ROTA DE BACKUP: Retorna a contagem atual como JSON (Para o JS se o SSE falhar)
@app.route('/get_count')
def get_count():
    global detector
    count = detector.counter if detector else 0
    return jsonify({'count': count})


def gen_frames():
    global camera, detector
    
    while True:
        # Garante que a câmera e o detector estejam prontos antes de processar
        if camera and detector:
            
            ret, frame = camera.get_frame() 
            
            if not ret or frame is None:
                time.sleep(0.1)
                continue
                
            with frame_lock:
                # TRATAMENTO DE ERRO ROBUSTO: Protege o stream contra falhas no detector
                try:
                    # 1. Processa o frame
                    processed_frame, count = detector.detect_and_tag(frame)
                    
                    if processed_frame is None:
                         time.sleep(0.1)
                         continue
                         
                    h, w, _ = processed_frame.shape
                    
                    # 2. CÓDIGO FINAL DE DESENHO DO CONTADOR (Fundo Preto/Letra Branca)
                    count_text = f"TOTAL: {count}"
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    
                    # Desenha Fundo
                    (text_w, text_h), baseline = cv2.getTextSize(count_text, font, 1.5, 3)
                    text_x = w - 400
                    text_y = 50 
                    cv2.rectangle(processed_frame, 
                                  (text_x - 10, text_y - text_h - 10), 
                                  (text_x + text_w + 10, text_y + baseline + 10), 
                                  (0, 0, 0), -1) # Fundo preto
                                  
                    # Desenha Texto
                    cv2.putText(processed_frame, count_text, (text_x, text_y), font, 1.5, (255, 255, 255), 3)
                    
                    # -------------------------------------

                    # 3. Codifica para JPEG e envia
                    ret, buffer = cv2.imencode('.jpg', processed_frame)
                
                    if ret:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                
                except Exception as e:
                    print(f"[ERRO NO PROCESSAMENTO DO FRAME] Detector falhou: {e}")
                    time.sleep(0.5)
        else:
            time.sleep(0.5)


@app.route('/')
def index():
    state = load_state()
    
    selected_camera_id = state.get("camera_id", "CAM1")
    total_count = detector.counter if detector else 0
    
    return render_template(
        'index.html',
        status=state["status"],
        lote=state["lote"],
        data=state["data"],
        hora_inicio=state["hora_inicio"],
        total_count=total_count, 
        source=state["source_path"],
        
        camera_options=CAMERA_OPTIONS,
        selected_camera=selected_camera_id
    )

@app.route('/start', methods=['POST'])
def start():
    state = load_state()
    
    lote = request.form['lote']
    source_type = request.form['source_type']
    camera_id = request.form.get('camera_id', 'CAM1') 
    
    config = CAMERA_OPTIONS.get(camera_id)
    if not config:
        flash("Configuração de câmera inválida.", "error")
        return redirect(url_for('index'))

    rtsp_url = config["rtsp_url"]
    roi_config = config["roi"]
    model_path = config["model"]

    agora = datetime.now()
    os.makedirs("logs", exist_ok=True)
    log_file = os.path.join("logs", f"{lote}_{agora.strftime('%Y%m%d_%H%M%S')}.log")

    source_path = rtsp_url if source_type == 'rtsp' else request.form['video_file']

    state["status"] = "START"
    state["lote"] = lote
    state["data"] = agora.strftime("%d/%m/%Y")
    state["hora_inicio"] = agora.strftime("%H:%M:%S")
    state["log_file"] = log_file
    state["source_path"] = source_path
    state["camera_id"] = camera_id 
    
    save_state(state) 
    
    global camera, detector
    
    # GARANTINDO LIMPEZA DE OBJETOS ANTERIORES
    if camera:
        camera.release()
        camera = None
    detector = None

    try:
         # Inicializa a câmera e o detector
         camera = VideoSource(source_path)
         detector = IndustrialTagDetector(
             model_path=model_path,
             roi=roi_config, 
             log_file=log_file
         )
         flash(f"Contagem iniciada para {config['name']} (Lote: {lote}).", "success")
         # REDIRECT PADRÃO: Usado em conjunto com a lógica SSE para iniciar o contador
         return redirect(url_for('index'))
         
    except Exception as e:
         flash(f"Erro ao iniciar o stream ou detector: {e}", "error")
         clear_state() 
         return redirect(url_for('index'))

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stop')
def stop():
    state = load_state()
    global camera, detector
    
    quantidade = detector.counter if detector else 0
    log_file_path = state.get("log_file")

    agora = datetime.now()
    hora_fim = agora.strftime("%H:%M:%S")
    
    salvar_contagem(state["lote"], state["data"], state["hora_inicio"], hora_fim, quantidade, log_file_path)

    if camera:
        camera.release()
        camera = None
    detector = None
    
    flash(f"Contagem finalizada. Total de sacos: {quantidade}", "info")
    clear_state()
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Lógica de RESTAURAÇÃO DE ESTADO
    state = load_state()
    if state["status"] == "START" and state.get("source_path"):
        source = state["source_path"]
        log_file = state["log_file"]
        camera_id = state.get("camera_id", "CAM1")
        
        config = CAMERA_OPTIONS.get(camera_id, CONFIG_CAMERA_1)
        roi_config = config["roi"]
        model_path = config["model"]
        
        try:
             camera = VideoSource(source)
             detector = IndustrialTagDetector(
                 model_path=model_path,
                 roi=roi_config,
                 log_file=log_file
             )
             print(f"Estado restaurado. Contagem rodando em {config['name']}.")
        except Exception as e:
             print(f"Falha ao restaurar o estado: {e}. O sistema será limpo.")
             clear_state()
    
    app.run(host='0.0.0.0', port=8080, debug=True, threaded=True)