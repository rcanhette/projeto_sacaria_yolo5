# services/runtime.py
tc_runtime = {}  # { tc_id: CapturePoint }

def drop_tc_runtime(tc_id:int):
    cp = tc_runtime.pop(tc_id, None)
    if cp:
        try:
            cp.release()
        except Exception:
            pass

# Backward aliases (temporary, to ease migration)
ct_runtime = tc_runtime
def drop_ct_runtime(ct_id:int):
    return drop_tc_runtime(ct_id)
