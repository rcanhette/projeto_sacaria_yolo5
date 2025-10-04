# services/runtime.py
ct_runtime = {}  # { ct_id: CapturePoint }

def drop_ct_runtime(ct_id:int):
    cp = ct_runtime.pop(ct_id, None)
    if cp:
        try:
            cp.release()
        except Exception:
            pass
