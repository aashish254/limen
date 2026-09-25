def retry(charge_id, attempts=3):
    for i in range(attempts):
        try:
            return _charge(charge_id)
        except ConnectionError:
            continue
    raise RuntimeError("retry exhausted")


def _charge(charge_id):
    return {"id": charge_id, "status": "captured"}
