import os

TUYA_ENABLED = os.environ.get('TUYA_ENABLED', '0') in ('1','true','True')
TUYA_TOKEN = os.environ.get('TUYA_TOKEN')
TUYA_DEVICE_IP = os.environ.get('TUYA_DEVICE_IP')
TUYA_DEVICE_ID = os.environ.get('TUYA_DEVICE_ID')
try:
    from .config import HA_URL, HA_TOKEN
except Exception:
    try:
        from config import HA_URL, HA_TOKEN
    except Exception:
        HA_URL = os.environ.get('HA_URL')
        HA_TOKEN = os.environ.get('HA_TOKEN')


def perform_action(device_id: str, action: str) -> (bool, str):
    """
    Intenta ejecutar la acción 'encender'|'apagar' sobre el dispositivo tuya identificado por device_id.
    Si TUYA_ENABLED está desactivado, emula éxito.
    Retorna (success: bool, message: str).
    """
    action = action.lower()
    if action not in ('encender', 'apagar', 'on', 'off'):
        return False, f"acción desconocida {action}"

    if not TUYA_ENABLED:
        # if device_id looks like a Home Assistant entity (contains a dot)
        # and HA_TOKEN is available, call HA service instead of emulating
        if device_id and '.' in device_id and HA_URL and HA_TOKEN:
            svc = 'turn_on' if action in ('encender', 'on') else 'turn_off'
            try:
                import urllib.request, json
                url = f"{HA_URL}/api/services/switch/{svc}"
                req = urllib.request.Request(url, data=json.dumps({'entity_id': device_id}).encode('utf8'),
                                             headers={'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'})
                print(f"tuya_client: calling HA service {svc} for entity {device_id}")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code = resp.getcode()
                    print(f"tuya_client: HA service response code={code}")
                    return (200 <= code < 300), f'called HA service {svc} status={code}'
            except Exception as e:
                print(f"tuya_client: HA call error: {e}")
                return False, f'ha call error: {e}'
        return True, 'emulated: TUYA_ENABLED not set'

    try:
        import tinytuya
    except Exception as e:
        return False, f'tinytuya import error: {e}'

    # prefer device-specific env vars if provided
    token = os.environ.get('TUYA_TOKEN') or TUYA_TOKEN
    device_ip = os.environ.get('TUYA_DEVICE_IP') or TUYA_DEVICE_IP
    device_id = os.environ.get('TUYA_DEVICE_ID') or TUYA_DEVICE_ID or device_id

    if not all((token, device_ip, device_id)):
        return False, 'missing TUYA_TOKEN/TUYA_DEVICE_ID/TUYA_DEVICE_IP'

    try:
        d = tinytuya.BulbDevice(device_id, device_ip, token)
        if action in ('encender', 'on'):
            d.turn_on()
        else:
            d.turn_off()
        return True, 'called tinytuya'
    except Exception as e:
        return False, f'tinytuya call error: {e}'


def perform_pulse(device_id: str, duration_ms: int = 500) -> (bool, str):
    """Enciende el dispositivo, espera duration_ms milisegundos y lo apaga.
    Retorna (success, message) donde success es True si ambos comandos (on y off)
    fueron exitosos (o emulados) y message contiene info.
    """
    # encender
    print(f"tuya_client.perform_pulse: device={device_id} duration_ms={duration_ms} - starting pulse")
    ok_on, msg_on = perform_action(device_id, 'encender')
    # esperar
    try:
        import time
        time.sleep(max(0, duration_ms) / 1000.0)
    except Exception:
        pass
    # apagar
    ok_off, msg_off = perform_action(device_id, 'apagar')

    success = ok_on and ok_off
    msg = f'on: {msg_on}; off: {msg_off}'
    print(f"tuya_client.perform_pulse: device={device_id} result success={success} msg={msg}")
    return success, msg
