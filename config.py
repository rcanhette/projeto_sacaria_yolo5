SECRET_KEY = "supersecret"

# Lista de CTs disponíveis (fixo p/ testes)
CT_LIST = {
    1: {
        "id": 1,
        "name": "CT 1",
        "source_path": "rtsp://admin:Coop%402020@172.16.10.83:554/Streaming/Channels/101",
        "roi": "765,495,300,375",
        "model_path": "sacaria_yolov5n.pt",
    },
    2: {
        "id": 2,
        "name": "CT 2",
        "source_path": "rtsp://192.168.1.102:554/stream",
        "roi": "100,100,500,400",
        "model_path": "sacaria_yolov5n.pt",
    },
}


# Qual CT abre na raiz
DEFAULT_CT_ID = 1
