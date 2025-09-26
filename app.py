import os
import json
from flask import Flask, render_template, Response, request, redirect, url_for
import cv2
from datetime import datetime
from industrial_tag_detector import IndustrialTagDetector
import time

# IMPORTAÇÃO CORRIGIDA: Usa a classe VideoSource que tem a solução de threading
from video_source import VideoSource 

# Filtro de Aviso (para ser usado com 'python -W ignore app.py')
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

# Fontes de Vídeo
RTSP_URL = "rtsp://admin:Coop%402020@172.16.10.83:554/Streaming/Channels/101"

# ROI: (x_inicial, y_inicial, largura, altura)
# ATENÇÃO: Configurado para 1920x1080 (escala de 1.5x o antigo 1280x720)
ROI_ORIGINAL = (765, 495, 300, 375)
# A linha abaixo foi o antigo ROI (0, 0, 0, 0) que desativa o ROI
#ROI_ORIGINAL = (0, 0, 0, 0)

camera = None 
detector = None

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "status": "STOP",
            "lote": "",
            "data": "",
            "hora_inicio": "",
            "log_file": None,
            "source_path": RTSP_URL 
        }
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
        if "source_path" not in state:
             state["source_path"] = RTSP_URL
        return state

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def clear_state():
    save_state({
        "status": "STOP",
        "lote": "",
        "data": "",
        "hora_inicio": "",
        "log_file": None,
        "source_path": RTSP_URL 
    })

def salvar_contagem(lote, data, hora_inicio, hora_fim, quantidade, log_file_path):
    # Log completo
    final_filename = f"log_contagem_{lote}_{data.replace('/', '-')}_{hora_inicio.replace(':', '-')}.txt"
    linha_resumo = f"lote: {lote} | Data: {data} | Hora Início: {hora_inicio} | Hora Fim: {hora_fim} | Quantidade: {quantidade}\n"
    
    try:
        if log_file_path and os.path.exists(log_file_path):
            with open(log_file_path, 'r', encoding='utf-8') as f:
                conteudo_log_detalhado = f.read()
            
            conteudo_final = linha_resumo + conteudo_log_detalhado
            
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(conteudo_final)
            
            os.rename(log_file_path, final_filename)
        else:
             with open(final_filename, 'w', encoding='utf-8') as f:
                 f.write(linha_resumo)

    except Exception as e:
        print(f"[ERRO CONSOLIDAÇÃO] Falha ao consolidar ou renomear log: {e}")
        with open(final_filename, 'a', encoding='utf-8') as f:
            f.write(linha_resumo)

def gen_frames():
    global camera, detector
    while True:
        if camera and detector:
            ret, frame = camera.get_frame()
            if not ret or frame is None:
                # Se falhar, tenta pegar um frame em branco, agora em 1920x1080
                import numpy as np 
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8) 
                cv2.putText(frame, "ERRO: CAMERA OFFLINE OU FIM DO VIDEO", (50, 360), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)
            else:
                frame, total_count = detector.detect_and_tag(frame)
                
              # ==========================================================
                # NOVO DESTAQUE: Fundo Preto e Texto Branco
                # ==========================================================
                text = f"TOTAL: {total_count}"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 1.5
                thickness = 4
                
                # Posição de Início do Texto
                position_x, position_y = 20, 60 
                
                # 1. Calcular o tamanho que o texto ocupa
                # text_size[0] = largura, text_size[1] = altura da fonte
                (text_width, text_height), baseline = cv2.getTextSize(text, font, font_scale, thickness)
                
                # 2. Desenhar o Fundo (Retângulo Preto)
                # Ponto de início (canto superior esquerdo do retângulo)
                rect_start = (position_x - 10, position_y - text_height - 10)
                # Ponto final (canto inferior direito do retângulo)
                rect_end = (position_x + text_width + 10, position_y + baseline + 10)
                
                # Desenha o retângulo preenchido (cor BGR: Preto = 0, 0, 0)
                cv2.rectangle(frame, rect_start, rect_end, (0, 0, 0), -1) 
                
                # 3. Desenhar o Texto Principal (Branco)
                # A cor (255, 255, 255) é BGR (Azul, Verde, Vermelho)
                cv2.putText(frame, text, (position_x, position_y), font, font_scale, (255, 255, 255), thickness) 
                
                # ==========================================================
                
            # Redimensiona para o display do navegador (800x600)
            display_frame = cv2.resize(frame, (800, 600)) 
            
            ret, buffer = cv2.imencode('.jpg', display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            import numpy as np
            frame = np.zeros((600, 800, 3), dtype=np.uint8)
            cv2.putText(frame, "Contagem Parada. Clique em 'Start'", (50, 300), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(1) 

@app.route('/', methods=['GET', 'POST'])
def index():
    state = load_state()
    if state["status"] == "START":
        return redirect(url_for('contagem'))
        
    if request.method == 'POST':
        # 1. Obter dados do formulário
        lote = request.form['lote']
        source_type = request.form['source_type']
        source_input = request.form.get('source_input', '').strip()
        
        # 2. Definir a fonte de vídeo
        if source_type == 'rtsp':
            source = RTSP_URL
        elif source_type == 'local' and source_input:
            source = source_input
        else:
             # Caso default ou erro, usa RTSP
            source = RTSP_URL
            
        # 3. Inicializar Log e Detector
        agora = datetime.now()
        data = agora.strftime("%d/%m/%Y")
        hora_inicio = agora.strftime("%H:%M:%S")
        log_temp_name = f"temp_log_{lote}_{data.replace('/', '-')}_{hora_inicio.replace(':', '-')}.txt"
        
        global camera, detector
        camera = VideoSource(source) 
        detector = IndustrialTagDetector(model_path=MODEL_PATH, roi=ROI_ORIGINAL, log_file=log_temp_name)
        
        # 4. Salvar estado
        state = {
            "status": "START",
            "lote": lote,
            "data": data,
            "hora_inicio": hora_inicio,
            "log_file": log_temp_name,
            "source_path": source 
        }
        save_state(state)
        
        return redirect(url_for('contagem'))
        
    return render_template('index.html', last_source=state["source_path"])

@app.route('/contagem')
def contagem():
    state = load_state()
    global detector
    total_count = detector.counter if detector else 0 
    
    return render_template(
        'contagem.html',
        lote=state["lote"],
        data=state["data"],
        hora_inicio=state["hora_inicio"],
        # A contagem total agora é passada uma vez, no carregamento da página
        total_count=total_count, 
        source=state["source_path"]
    )

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
    clear_state()
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    state = load_state()
    if state["status"] == "START" and state.get("source_path"):
        source = state["source_path"]
        log_file = state["log_file"]
        
        try:
             camera = VideoSource(source)
             detector = IndustrialTagDetector(model_path=MODEL_PATH, roi=ROI_ORIGINAL, log_file=log_file)
        except Exception as e:
             print(f"[AVISO] Não foi possível restaurar a sessão anterior: {e}")
             clear_state()
             
    # Execução na porta 8080
    app.run(debug=True, threaded=True, port=8080)