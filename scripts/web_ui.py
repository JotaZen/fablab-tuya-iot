#!/usr/bin/env python3
"""Servidor web para UI y endpoints REST/WS.
- GET / => UI estática
- GET /models => modelos actuales
- WS /ws => eventos push
- POST /rfid => procesa lecturas (carga por estación y liquidación en lector)
- Breakers: toggle/set/pulse y consumo
- Tarjetas: actualizar saldo manual
"""
import os
import re
import json
import time
import asyncio
from typing import Set

from aiohttp import web
import websockets

# --------- Imports con fallbacks ---------
try:
    from .models_loader import (
        get_models, get_breaker, toggle_breaker, set_breaker_state,
        get_tarjeta_for_breaker, update_breaker_fields,
        set_tarjeta_saldo, adjust_tarjeta_saldo
    )
except Exception:
    try:
        from scripts.models_loader import (
            get_models, get_breaker, toggle_breaker, set_breaker_state,
            get_tarjeta_for_breaker, update_breaker_fields,
            set_tarjeta_saldo, adjust_tarjeta_saldo
        )
    except Exception:
        from models_loader import (
            get_models, get_breaker, toggle_breaker, set_breaker_state,
            get_tarjeta_for_breaker, update_breaker_fields,
            set_tarjeta_saldo, adjust_tarjeta_saldo
        )

try:
    from .breaker_service import set_breaker, toggle_breaker_service, pulse_breaker_service
except Exception:
    try:
        from breaker_service import set_breaker, toggle_breaker_service, pulse_breaker_service
    except Exception:
        async def toggle_breaker_service(*args, **kwargs):
            return {'ok': False, 'error': 'breaker_service unavailable'}
        async def set_breaker(*args, **kwargs):
            return {'ok': False, 'error': 'breaker_service unavailable'}
        async def pulse_breaker_service(*args, **kwargs):
            return {'ok': False, 'error': 'breaker_service unavailable'}

# Config HA
try:
    from .config import HA_URL as CFG_HA_URL, HA_TOKEN as CFG_HA_TOKEN
except Exception:
    try:
        from config import HA_URL as CFG_HA_URL, HA_TOKEN as CFG_HA_TOKEN
    except Exception:
        CFG_HA_URL = os.environ.get('HA_URL') or 'http://localhost:8123'
        CFG_HA_TOKEN = os.environ.get('HA_TOKEN') or ''

API_KEY = os.environ.get('API_KEY')
BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, 'data.json')
USAGE_LIMITS_PATH = os.path.join(BASE_DIR, 'usage_limits.json')
HA_URL = CFG_HA_URL
HA_TOKEN = CFG_HA_TOKEN
HA_WS = os.getenv('HA_WS') or (HA_URL.replace('http', 'ws') + '/api/websocket')

# --------- Estado y utilidades ---------
class ServerState:
    def __init__(self):
        self.websockets: Set[web.WebSocketResponse] = set()

    async def broadcast(self, message: dict):
        txt = json.dumps(message, ensure_ascii=False)
        stale = []
        for ws in list(self.websockets):
            try:
                await ws.send_str(txt)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.websockets.discard(ws)


state = ServerState()


def load_models():
    try:
        with open(DATA_PATH, 'r', encoding='utf8') as f:
            return json.load(f)
    except Exception:
        return {"tarjetas": [], "breakers": [], "arduinos": []}


def load_usage_limits():
    """Cargar límites de uso desde usage_limits.json"""
    try:
        with open(USAGE_LIMITS_PATH, 'r', encoding='utf8') as f:
            data = json.load(f)
            return data.get('limites', {
                'tiempo_profe_segundos': 1800,
                'tiempo_ia_segundos': 900,
                'max_usos_profe': 5,
                'max_usos_ia': 3
            })
    except Exception:
        return {
            'tiempo_profe_segundos': 1800,
            'tiempo_ia_segundos': 900,
            'max_usos_profe': 5,
            'max_usos_ia': 3
        }


def save_usage_limits(limits: dict):
    """Guardar límites de uso en usage_limits.json"""
    try:
        with open(USAGE_LIMITS_PATH, 'w', encoding='utf8') as f:
            json.dump({'limites': limits}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print('save_usage_limits error', e)
        return False


def save_models(models: dict):
    """Persistir models en DATA_PATH atomically."""
    try:
        # antes de persistir, eliminar campos de saldo en breakers para que
        # la fuente de verdad del saldo siga siendo `tarjetas`.
        try:
            for bb in models.get('breakers', []):
                bb.pop('saldo', None)
                bb.pop('max_saldo', None)
        except Exception:
            pass
        tmp = DATA_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump(models, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_PATH)
        return True
    except Exception as e:
        print('save_models error', e)
        return False


def init_models_startup():
    """Cargar y registrar un resumen de data.json al iniciar la app.
    Crea el archivo si no existe."""
    if not os.path.exists(DATA_PATH):
        with open(DATA_PATH, 'w', encoding='utf8') as f:
            json.dump({"tarjetas": [], "breakers": [], "arduinos": []}, f, ensure_ascii=False, indent=2)
        print(f"[startup] data.json no existía, creado en {DATA_PATH}")
    models = load_models()
    print(f"[startup] data.json cargado ({DATA_PATH}) -> breakers={len(models.get('breakers',[]))} tarjetas={len(models.get('tarjetas',[]))} arduinos={len(models.get('arduinos',[]))}")
    return models


async def watch_data_file(app):
    # snapshot previo para diffs de tarjetas
    prev = None
    try:
        prev = load_models()
    except Exception:
        prev = None
    while True:
        await asyncio.sleep(5)  # Reducido a 5 segundos para menos carga
        # cargar siempre: en Windows la resolución de mtime puede ser gruesa y perder cambios rápidos
        try:
            models = load_models()
        except Exception:
            continue
        # diffs de tarjetas (saldo) - solo broadcast si hay cambios
        try:
            prev_t = {t.get('id'): t for t in (prev.get('tarjetas', []) if prev else [])}
            cur_t = {t.get('id'): t for t in models.get('tarjetas', [])}
            has_changes = False
            for tid, cur in cur_t.items():
                pv = prev_t.get(tid)
                if pv is None:
                    continue
                try:
                    if float(cur.get('saldo') or 0.0) != float(pv.get('saldo') or 0.0):
                        asyncio.create_task(state.broadcast({'type': 'tarjetas:update', 'id': tid, 'tarjeta': cur}))
                        has_changes = True
                except Exception:
                    pass
            prev = models
        except Exception:
            prev = models
        # broadcast completo del modelo SOLO si hay clientes conectados y no se envió tarjetas:update
        if state.websockets and not has_changes:
            await state.broadcast({'type': 'models', 'data': models})


async def index(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'static', 'index.html'))


async def display(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'static', 'display.html'))


async def models_handler(request):
    models = load_models()
    return web.json_response(models)


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    state.websockets.add(ws)
    await ws.send_str(json.dumps({'type': 'models', 'data': load_models()}, ensure_ascii=False))
    await ws.send_str(json.dumps({'type': 'info', 'msg': 'cliente conectado'}, ensure_ascii=False))
    try:
        async for _ in ws:
            pass
    finally:
        state.websockets.discard(ws)
    return ws


async def rfid_post(request):
    # API key (opcional)
    if API_KEY:
        key = request.headers.get('X-API-KEY')
        if not key or key != API_KEY:
            return web.json_response({'ok': False, 'error': 'invalid api key'}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = await request.post()
        data = dict(data)

    # aceptar diferentes nombres desde distintos arduinos/firmwares
    uid = data.get('uid') or data.get('rfid') or data.get('nfc') or data.get('card') or data.get('tag')
    origen = data.get('origen') or data.get('arduino') or data.get('arduino_id') or data.get('id')

    # log completo
    print('RFID received:', json.dumps(data, ensure_ascii=False))

    # guardar ultimo dato en el arduino si existe
    matched = None
    if origen:
        try:
            models = load_models()
            arduinos = models.get('arduinos', [])
            for ad in arduinos:
                if ad.get('id') == origen:
                    matched = ad
                    break
            if matched is None:
                for ad in arduinos:
                    if ad.get('id') == data.get('arduino'):
                        matched = ad
                        break
            if matched is not None:
                if bool(matched.get('es_estacion_carga')):
                    # Estación de carga: actualizar 'charging' (lista de sesiones) y 'last'
                    now_ms = int(time.time() * 1000)
                    payload = dict(data)
                    payload.setdefault('ts', now_ms)
                    matched['last'] = payload
                    uid_seen = payload.get('uid') or payload.get('rfid') or payload.get('nfc')
                    # inicializar lista de sesiones si no existe
                    charging = matched.get('charging')
                    if not isinstance(charging, list):
                        charging = []
                        matched['charging'] = charging
                    if uid_seen:
                        # buscar sesión existente por UID
                        sess = None
                        for s in charging:
                            if s.get('uid') == uid_seen:
                                sess = s
                                break
                        if sess is None:
                            # crear nueva sesión
                            try:
                                wps = float(matched.get('w_por_segundo') or 0.0)
                            except Exception:
                                wps = 0.0
                            sess = {
                                'uid': uid_seen,
                                'started_ms': int(payload.get('ts') or payload.get('timestamp') or now_ms),
                                'wps': wps,
                                'last': payload,
                            }
                            charging.append(sess)
                        else:
                            # actualizar última lectura, no pisar started_ms si ya existe
                            sess['last'] = payload
                            if not sess.get('started_ms'):
                                sess['started_ms'] = int(payload.get('ts') or payload.get('timestamp') or now_ms)
                            if 'wps' not in sess or sess.get('wps') in (None, 0, 0.0):
                                try:
                                    sess['wps'] = float(matched.get('w_por_segundo') or 0.0)
                                except Exception:
                                    sess['wps'] = 0.0
                    if save_models(models):
                        asyncio.create_task(state.broadcast({'type': 'arduinos:update', 'id': matched.get('id'), 'arduino': matched}))
                else:
                    # Lector normal: liquidar carga si existe en cualquier estación
                    uid_seen = data.get('uid') or data.get('rfid') or data.get('nfc')
                    if uid_seen:
                        total = 0.0
                        now_ms = int(time.time() * 1000)
                        for ad in arduinos:
                            try:
                                if not bool(ad.get('es_estacion_carga')):
                                    continue
                                # Liquidar sesiones en lista 'charging' que coincidan con uid
                                charging = ad.get('charging') if isinstance(ad.get('charging'), list) else []
                                remaining = []
                                for s in charging:
                                    try:
                                        if s.get('uid') != uid_seen:
                                            remaining.append(s)
                                            continue
                                        wps = float(s.get('wps') if s.get('wps') is not None else (ad.get('w_por_segundo') or 0.0))
                                        started = int(s.get('started_ms') or now_ms)
                                        elapsed_ms = max(0, now_ms - started)
                                        total += wps * (elapsed_ms / 1000.0)
                                    except Exception:
                                        # si hay problema, no sumar y descartar sesión para evitar loops
                                        pass
                                if remaining or ('charging' in ad):
                                    ad['charging'] = remaining
                                # fallback adicional por 'last' si no había sesión (retrocompatibilidad)
                                if not charging:
                                    last = ad.get('last') or {}
                                    nfc = last.get('nfc') or last.get('uid') or last.get('rfid')
                                    if nfc == uid_seen:
                                        try:
                                            ts = int(last.get('ts') or last.get('timestamp') or (now_ms - 1000))
                                        except Exception:
                                            ts = now_ms - 1000
                                        elapsed_ms = max(0, now_ms - ts)
                                        try:
                                            wps_fb = float(ad.get('w_por_segundo') or 0.0)
                                        except Exception:
                                            wps_fb = 0.0
                                        total += wps_fb * (elapsed_ms / 1000.0)
                                        ad['last'] = None
                            except Exception:
                                continue
                        if total > 0:
                            tarjetas = models.get('tarjetas', [])
                            t = next((t for t in tarjetas if t.get('id') == uid_seen), None)
                            if t is not None:
                                try:
                                    current = float(t.get('saldo') or 0.0)
                                except Exception:
                                    current = 0.0
                                t['saldo'] = round(current + total, 6)
                                asyncio.create_task(state.broadcast({'type': 'tarjetas:update', 'id': uid_seen, 'tarjeta': t}))
                        if save_models(models):
                            for ad in arduinos:
                                if bool(ad.get('es_estacion_carga')):
                                    asyncio.create_task(state.broadcast({'type': 'arduinos:update', 'id': ad.get('id'), 'arduino': ad}))
                    matched['last'] = data
                    save_models(models)
        except Exception as e:
            print('rfid_post save arduino error', e)

    # asociar lectura con tarjeta si se detectó uid (soporta campo 'nfc' enviado por Arduino)
    if uid:
        try:
            models = load_models()
            tarjetas = models.get('tarjetas', [])
            tarjeta = next((t for t in tarjetas if t.get('id') == uid), None)
            if tarjeta:
                # notificar escaneo de tarjeta
                asyncio.create_task(state.broadcast({'type': 'tarjetas:scanned', 'tarjeta': tarjeta, 'origen': origen, 'arduino_last': matched.get('last') if matched else None}))
                # controlar breakers asociados: si la tarjeta tiene saldo > 0 encender, si no apagar
                try:
                    for b in models.get('breakers', []):
                        if b.get('tarjeta') == tarjeta.get('id'):
                            desired = float(tarjeta.get('saldo') or 0.0) > 0.0
                            if bool(b.get('estado')) != desired:
                                # set_breaker_state persistirá y realizará acciones externas
                                try:
                                    set_breaker_state(DATA_PATH, b.get('id'), desired)
                                except Exception:
                                    print('rfid_post: set_breaker_state error')
                                asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': b.get('id'), 'state': 'on' if desired else 'off'}))
                except Exception as e:
                    print('rfid_post set_breaker error', e)
        except Exception as e:
            print('rfid_post tarjeta association error', e)

    asyncio.create_task(state.broadcast({'type': 'rfid', 'uid': uid, 'origen': origen, 'data': data}))

    return web.json_response({'ok': True, 'received': data, 'uid': uid, 'origen': origen})


async def breaker_toggle_handler(request):
    bid = request.match_info.get('id')
    svc_res = await toggle_breaker_service(DATA_PATH, bid)
    if not svc_res.get('ok'):
        return web.json_response({'ok': False, 'error': svc_res.get('error')}, status=404)
    br = svc_res.get('breaker')
    # device identifier for Tuya: device_id, tuya_device or entity_id as fallback
    device_ident = br.get('device_id') or br.get('tuya_device') or br.get('entity_id')
    # broadcast tuya/ha results for UI
    if svc_res.get('tuya') is not None:
        t = dict(svc_res['tuya'])
        # ensure action key
        action = t.get('action') or 'toggle'
        # normalize ok -> success for frontend
        if 'ok' in t:
            t['success'] = t.pop('ok')
        # include device identifier so UI can show the id used
        payload = {'type': 'tuya', 'breaker_id': bid, 'action': action, **t}
        if device_ident:
            payload['device'] = device_ident
        asyncio.create_task(state.broadcast(payload))
    if svc_res.get('ha') is not None:
        asyncio.create_task(state.broadcast({'type': 'ha', 'breaker_id': bid, 'result': svc_res['ha']}))
    asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': br['id'], 'state': 'on' if br.get('estado') else 'off'}))
    return web.json_response({'ok': True, 'id': br['id'], 'state': 'on' if br.get('estado') else 'off'})


async def breaker_set_handler(request):
    bid = request.match_info.get('id')
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.post())
    state_req = body.get('state')
    if state_req not in ('on', 'off'):
        return web.json_response({'ok': False, 'error': 'invalid state'}, status=400)
    svc_res = await set_breaker(DATA_PATH, bid, state_req == 'on')
    if not svc_res.get('ok'):
        return web.json_response({'ok': False, 'error': svc_res.get('error')}, status=404)
    br = svc_res.get('breaker')
    device_ident = br.get('device_id') or br.get('tuya_device') or br.get('entity_id')
    if svc_res.get('tuya') is not None:
        t = dict(svc_res['tuya'])
        if 'ok' in t:
            t['success'] = t.pop('ok')
        # ensure action present (set_breaker uses action key)
        action = t.get('action')
        payload = {'type': 'tuya', 'breaker_id': bid, **t}
        if action:
            payload['action'] = action
        if device_ident:
            payload['device'] = device_ident
        asyncio.create_task(state.broadcast(payload))
    if svc_res.get('ha') is not None:
        asyncio.create_task(state.broadcast({'type': 'ha', 'breaker_id': bid, 'result': svc_res['ha']}))
    asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': br['id'], 'state': state_req}))
    return web.json_response({'ok': True, 'id': br['id'], 'state': state_req})


async def tarjeta_update_saldo(request):
    """Actualizar saldo de una tarjeta: POST /tarjetas/{id}/saldo { saldo: 12.34 }

    Se persiste en data.json y se hace broadcast de la tarjeta actualizada.
    """
    tid = request.match_info.get('id')
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.post())
    if 'saldo' not in body:
        return web.json_response({'ok': False, 'error': 'missing saldo'}, status=400)
    try:
        val = body.get('saldo')
        if isinstance(val, str):
            val = val.strip().replace(',', '.')
        try:
            val = float(val)
        except Exception:
            return web.json_response({'ok': False, 'error': 'invalid saldo'}, status=400)

        t = set_tarjeta_saldo(DATA_PATH, tid, val)
        if t is None:
            return web.json_response({'ok': False, 'error': 'unknown tarjeta'}, status=404)

        # broadcast de la tarjeta y de breakers asociados (su estado pudo cambiar)
        asyncio.create_task(state.broadcast({'type': 'tarjetas:update', 'id': t.get('id'), 'tarjeta': t}))
        try:
            models = load_models()
            for b in models.get('breakers', []):
                if b.get('tarjeta') == t.get('id'):
                    asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': b.get('id'), 'state': 'on' if b.get('estado') else 'off'}))
        except Exception:
            pass

        return web.json_response({'ok': True, 'tarjeta': t})
    except Exception as e:
        print('tarjeta_update_saldo error', e)
        return web.json_response({'ok': False, 'error': 'internal error'}, status=500)


async def tarjeta_adjust_saldo(request):
    """Ajusta el saldo de una tarjeta sumando un delta (puede ser negativo).

    POST /tarjetas/{id}/ajuste { delta: -12.34 }
    - Persiste en data.json usando adjust_tarjeta_saldo
    - Broadcast tarjetas:update y breakers:update para asociados
    - Si el saldo llega a 0, intenta apagar físicamente los breakers asociados
    """
    tid = request.match_info.get('id')
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.post())
    if 'delta' not in body:
        return web.json_response({'ok': False, 'error': 'missing delta'}, status=400)
    try:
        delta = body.get('delta')
        if isinstance(delta, str):
            delta = delta.strip().replace(',', '.')
        delta = float(delta)
    except Exception:
        return web.json_response({'ok': False, 'error': 'invalid delta'}, status=400)

    try:
        t = adjust_tarjeta_saldo(DATA_PATH, tid, delta)
        if t is None:
            return web.json_response({'ok': False, 'error': 'unknown tarjeta'}, status=404)

        # broadcast tarjeta actualizada
        asyncio.create_task(state.broadcast({'type': 'tarjetas:update', 'id': t.get('id'), 'tarjeta': t}))

        # revisar breakers asociados para notificar y apagar físicamente si corresponde
        try:
            models = load_models()
            for b in models.get('breakers', []):
                if b.get('tarjeta') == t.get('id'):
                    # notificar estado actual
                    asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': b.get('id'), 'state': 'on' if b.get('estado') else 'off'}))
                    # si saldo 0 y está ON, intentar apagado físico vía servicio
                    try:
                        saldo_val = float(t.get('saldo') or 0.0)
                    except Exception:
                        saldo_val = 0.0
                    if saldo_val <= 0.0 and bool(b.get('estado')):
                        try:
                            svc_res = await set_breaker(DATA_PATH, b.get('id'), False)
                            if svc_res.get('ok'):
                                asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': b.get('id'), 'state': 'off', 'reason': 'saldo=0'}))
                        except Exception:
                            pass
        except Exception:
            pass

        return web.json_response({'ok': True, 'tarjeta': t})
    except Exception as e:
        print('tarjeta_adjust_saldo error', e)
        return web.json_response({'ok': False, 'error': 'internal error'}, status=500)


async def breaker_pulse_handler(request):
    bid = request.match_info.get('id')
    br = get_breaker(DATA_PATH, bid)
    if not br:
        return web.json_response({'ok': False, 'error': 'unknown breaker'}, status=404)
    device_id = br.get('device_id') or br.get('tuya_device') or br.get('entity_id') or ''

    svc_res = await pulse_breaker_service(DATA_PATH, bid, 500)
    if not svc_res.get('ok'):
        return web.json_response({'ok': False, 'error': svc_res.get('error')}, status=404)

    # broadcast resultados
    if svc_res.get('tuya') is not None:
        t = dict(svc_res.get('tuya', {}))
        if 'ok' in t:
            t['success'] = t.pop('ok')
        t['action'] = t.get('action') or 'pulse'
        payload = {'type': 'tuya', 'breaker_id': bid, **t}
        if device_id:
            payload['device'] = device_id
        asyncio.create_task(state.broadcast(payload))
    if svc_res.get('ha') is not None:
        asyncio.create_task(state.broadcast({'type': 'ha', 'breaker_id': bid, 'result': svc_res['ha']}))

    return web.json_response({'ok': True, 'id': br.get('id'), 'pulse': True})


async def breaker_refresh_handler(request):
    """Fuerza actualización de métricas de un breaker consultando Home Assistant.
    
    POST /breakers/{id}/refresh
    
    Lee el estado actual de todas las entidades del breaker en HA y actualiza localmente.
    """
    bid = request.match_info.get('id')
    br = get_breaker(DATA_PATH, bid)
    if not br:
        return web.json_response({'ok': False, 'error': 'unknown breaker'}, status=404)
    
    entity_id = br.get('entity_id')
    if not entity_id or not HA_URL or not HA_TOKEN:
        return web.json_response({'ok': False, 'error': 'ha_not_configured'}, status=400)
    
    # Recopilar todas las entidades a consultar
    entities_to_query = set()
    if entity_id:
        entities_to_query.add(entity_id)
    
    # Agregar entidades configuradas explícitamente
    for key in ('power_entity', 'voltage_entity', 'current_entity', 'energy_entity'):
        val = br.get(key)
        if val:
            entities_to_query.add(val)
    
    # Agregar lista de entidades
    extra = br.get('entities') or []
    if isinstance(extra, list):
        for e in extra:
            if isinstance(e, str):
                entities_to_query.add(e)
    
    if not entities_to_query:
        return web.json_response({'ok': False, 'error': 'no_entities_configured'}, status=400)
    
    # Consultar Home Assistant
    import aiohttp
    headers = {'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'}
    
    updated_fields = {}
    errors = []
    
    try:
        async with aiohttp.ClientSession() as session:
            for ent_id in entities_to_query:
                try:
                    async with session.get(f"{HA_URL}/api/states/{ent_id}", headers=headers) as resp:
                        if resp.status != 200:
                            errors.append(f"{ent_id}: status {resp.status}")
                            continue
                        
                        state_data = await resp.json()
                        state = state_data.get('state')
                        attrs = state_data.get('attributes') or {}
                        
                        # Función auxiliar para extraer numérico
                        def extract_numeric(val):
                            try:
                                if val is None:
                                    return None
                                if isinstance(val, (int, float)):
                                    return val
                                return float(str(val))
                            except Exception:
                                return None
                        
                        # Actualizar estado si es el switch principal
                        if ent_id == entity_id:
                            if state in ('on', 'off'):
                                new_state_bool = (state == 'on')
                                if bool(br.get('estado')) != new_state_bool:
                                    set_breaker_state(DATA_PATH, bid, new_state_bool)
                                    updated_fields['estado'] = new_state_bool
                        
                        # Detectar tipo de sensor y actualizar métricas
                        lower_eid = ent_id.lower()
                        
                        # Corriente
                        if 'corriente' in lower_eid or 'current' in lower_eid:
                            current = extract_numeric(state) or extract_numeric(attrs.get('current'))
                            if current is not None:
                                updated_fields['current'] = current
                        
                        # Voltaje
                        if 'tension' in lower_eid or 'voltage' in lower_eid:
                            voltage = extract_numeric(state) or extract_numeric(attrs.get('voltage'))
                            if voltage is not None:
                                updated_fields['voltage'] = voltage
                        
                        # Potencia
                        if 'potencia' in lower_eid or 'power' in lower_eid:
                            power = extract_numeric(state) or extract_numeric(attrs.get('power'))
                            if power is not None:
                                updated_fields['power'] = power
                        
                        # Energía
                        if 'energia' in lower_eid or 'energy' in lower_eid:
                            energy = extract_numeric(state) or extract_numeric(attrs.get('energy'))
                            if energy is not None:
                                updated_fields['energy'] = energy
                        
                except Exception as e:
                    errors.append(f"{ent_id}: {str(e)}")
    
    except Exception as e:
        return web.json_response({'ok': False, 'error': f'ha_request_failed: {str(e)}'}, status=500)
    
    # Actualizar campos en data.json
    if updated_fields:
        try:
            # Separar estado de métricas
            estado = updated_fields.pop('estado', None)
            if estado is not None:
                # Ya se actualizó con set_breaker_state
                asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': bid, 'state': 'on' if estado else 'off'}))
            
            if updated_fields:  # Si quedan métricas
                update_breaker_fields(DATA_PATH, bid, **updated_fields)
                asyncio.create_task(state.broadcast({'type': 'breakers:consumption', 'id': bid, **updated_fields}))
        except Exception as e:
            errors.append(f"save_error: {str(e)}")
    
    return web.json_response({
        'ok': True,
        'id': bid,
        'updated': updated_fields,
        'errors': errors if errors else None
    })

async def breakers_consumption_handler(request):
    """Devuelve métricas básicas de consumo de todos los breakers."""
    models = load_models()
    out = []
    for b in models.get('breakers', []):
        entry = {
            'id': b.get('id'),
            'estado': bool(b.get('estado')),
            'power': b.get('power'),
            'energy': b.get('energy'),
            'voltage': b.get('voltage'),
            'current': b.get('current'),
        }
        out.append(entry)
    return web.json_response({'ok': True, 'breakers': out})


async def usage_limits_handler(request):
    """GET /usage-limits - Obtener límites de uso configurados"""
    limits = load_usage_limits()
    return web.json_response({'ok': True, 'limits': limits})


async def usage_limits_update_handler(request):
    """POST /usage-limits - Actualizar límites de uso"""
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.post())
    
    limits = load_usage_limits()
    
    # Actualizar solo los campos proporcionados
    if 'tiempo_profe_segundos' in body:
        limits['tiempo_profe_segundos'] = int(body['tiempo_profe_segundos'])
    if 'tiempo_ia_segundos' in body:
        limits['tiempo_ia_segundos'] = int(body['tiempo_ia_segundos'])
    if 'max_usos_profe' in body:
        limits['max_usos_profe'] = int(body['max_usos_profe'])
    if 'max_usos_ia' in body:
        limits['max_usos_ia'] = int(body['max_usos_ia'])
    
    if save_usage_limits(limits):
        asyncio.create_task(state.broadcast({'type': 'usage_limits:update', 'limits': limits}))
        return web.json_response({'ok': True, 'limits': limits})
    else:
        return web.json_response({'ok': False, 'error': 'save failed'}, status=500)


async def breaker_usage_handler(request):
    """GET /breakers/{id}/usage - Obtener información de uso de un breaker"""
    bid = request.match_info.get('id')
    br = get_breaker(DATA_PATH, bid)
    if not br:
        return web.json_response({'ok': False, 'error': 'unknown breaker'}, status=404)
    
    usage = {
        'id': bid,
        'usos_profe': br.get('usos_profe', 0),
        'usos_ia': br.get('usos_ia', 0),
        'usando_ia_desde': br.get('usando_ia_desde'),
        'usando_profe_desde': br.get('usando_profe_desde'),
    }
    
    return web.json_response({'ok': True, 'usage': usage})


async def breaker_usage_update_handler(request):
    """POST /breakers/{id}/usage - Actualizar contadores de uso de un breaker
    
    Body: {
        "usos_profe": 2,
        "usos_ia": 1,
        "usando_ia_desde": timestamp_ms o null,
        "usando_profe_desde": timestamp_ms o null
    }
    """
    bid = request.match_info.get('id')
    br = get_breaker(DATA_PATH, bid)
    if not br:
        return web.json_response({'ok': False, 'error': 'unknown breaker'}, status=404)
    
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.post())
    
    fields = {}
    if 'usos_profe' in body:
        fields['usos_profe'] = int(body['usos_profe'])
    if 'usos_ia' in body:
        fields['usos_ia'] = int(body['usos_ia'])
    if 'usando_ia_desde' in body:
        val = body['usando_ia_desde']
        fields['usando_ia_desde'] = int(val) if val is not None else None
    if 'usando_profe_desde' in body:
        val = body['usando_profe_desde']
        fields['usando_profe_desde'] = int(val) if val is not None else None
    
    if fields:
        update_breaker_fields(DATA_PATH, bid, **fields)
        asyncio.create_task(state.broadcast({'type': 'breakers:usage_update', 'id': bid, **fields}))
        return web.json_response({'ok': True, 'id': bid, 'updated': fields})
    else:
        return web.json_response({'ok': False, 'error': 'no fields to update'}, status=400)


async def breaker_usage_reset_handler(request):
    """POST /breakers/{id}/usage/reset - Resetear contadores de uso de un breaker
    
    Query params:
        ?type=profe o ?type=ia o ambos si se omite
    """
    bid = request.match_info.get('id')
    br = get_breaker(DATA_PATH, bid)
    if not br:
        return web.json_response({'ok': False, 'error': 'unknown breaker'}, status=404)
    
    reset_type = request.query.get('type', 'all')
    
    fields = {}
    if reset_type in ('all', 'profe'):
        fields['usos_profe'] = 0
        fields['usando_profe_desde'] = None
    if reset_type in ('all', 'ia'):
        fields['usos_ia'] = 0
        fields['usando_ia_desde'] = None
    
    update_breaker_fields(DATA_PATH, bid, **fields)
    asyncio.create_task(state.broadcast({'type': 'breakers:usage_update', 'id': bid, **fields}))
    
    return web.json_response({'ok': True, 'id': bid, 'reset': reset_type, 'fields': fields})


_last_tick_time = 0  # timestamp del último tick procesado
_tick_min_interval = 2.0  # mínimo intervalo entre ticks (segundos) - aumentado para reducir carga

async def breakers_tick_consumption_handler(request):
    """Procesa consumo de todos los breakers en una sola llamada optimizada.
    
    Recorre todos los breakers encendidos, calcula su consumo W·s,
    y descuenta del saldo de su tarjeta asociada.
    
    POST /breakers/tick-consumption { elapsed: 1.0 }
    
    Retorna: { ok: true, processed: [...], errors: [...] }
    """
    global _last_tick_time
    import time
    
    now = time.time()
    if now - _last_tick_time < _tick_min_interval:
        print(f"[Tick] ⚠️ Petición duplicada ignorada (último tick hace {now - _last_tick_time:.2f}s)")
        return web.json_response({'ok': True, 'breakers': [], 'errors': [], 'skipped': 'duplicate_request'})
    
    _last_tick_time = now
    
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    elapsed = body.get('elapsed', 1.0)  # segundos transcurridos
    
    # Cargar modelos directamente (sin executor para evitar race conditions)
    models = load_models()
    
    breakers = models.get('breakers', [])
    
    processed = []
    errors = []
    
    # Agrupar descuentos por tarjeta para hacer una sola actualización por tarjeta
    tarjeta_deltas = {}  # {tarjeta_id: total_ws}
    breaker_updates = {}  # {breaker_id: consumption_last_ws}
    
    for br in breakers:
        if not br or not br.get('estado'):
            continue  # breaker apagado
        
        tarjeta_id = br.get('tarjeta')
        if not tarjeta_id:
            continue  # sin tarjeta asociada
        
        # Calcular potencia con lógica mejorada
        power = br.get('power')
        voltage = br.get('voltage')
        current = br.get('current')
        
        # Inferir potencia si falta usando V*I
        inferred = None
        try:
            if voltage is not None and current is not None:
                v_val = float(voltage)
                i_val = float(current)
                if v_val > 0 and i_val > 0:
                    inferred = v_val * i_val
        except Exception:
            pass
        
        # Determinar potencia final
        final_power = None
        try:
            if power is not None and power != '':
                p_val = float(power)
                # Si power es muy pequeño (<10W) probablemente está en kW, convertir
                if p_val < 10 and p_val > 0:
                    # Verificar si tiene sentido la conversión comparando con V*I
                    if inferred is not None and inferred > p_val * 100:
                        final_power = p_val * 1000.0  # kW a W
                    else:
                        final_power = p_val
                else:
                    final_power = p_val
            
            # Si no hay power o no es válido, usar inferido
            if final_power is None or final_power <= 0:
                final_power = inferred
                
        except Exception:
            final_power = inferred
        
        # Validar potencia final
        if final_power is None or final_power <= 0 or not isinstance(final_power, (int, float)):
            # Log para debugging
            print(f"[Tick] Breaker {br.get('id')} sin potencia válida: power={power} voltage={voltage} current={current} inferred={inferred}")
            continue
        
        ws = final_power * elapsed  # W·s consumidos
        
        # Acumular descuento por tarjeta
        if tarjeta_id not in tarjeta_deltas:
            tarjeta_deltas[tarjeta_id] = 0.0
        tarjeta_deltas[tarjeta_id] += ws
        
        # Guardar para actualización posterior
        breaker_updates[br.get('id')] = round(ws * 1000000) / 1000000
        
        processed.append({
            'breaker_id': br.get('id'),
            'tarjeta_id': tarjeta_id,
            'power': round(final_power * 1000000) / 1000000,
            'ws': round(ws * 1000000) / 1000000
        })
    
    # Actualizar consumption_last_ws en batch (sincrónico para evitar corrupción del JSON)
    if breaker_updates:
        for bid, ws_val in breaker_updates.items():
            try:
                update_breaker_fields(DATA_PATH, bid, consumption_last_ws=ws_val)
                # Broadcast actualización (este sí puede ser async)
                asyncio.create_task(state.broadcast({'type': 'breakers:consumption', 'id': bid, 'ws': ws_val}))
            except Exception as e:
                print(f"[Tick] Error actualizando breaker {bid}: {e}")
    
    # Aplicar descuentos agrupados por tarjeta (sincrónico para evitar race conditions)
    for tarjeta_id, total_ws in tarjeta_deltas.items():
        try:
            t = adjust_tarjeta_saldo(DATA_PATH, tarjeta_id, -total_ws)
            if t:
                # Broadcast actualización de tarjeta
                asyncio.create_task(state.broadcast({'type': 'tarjetas:update', 'id': t.get('id'), 'tarjeta': t}))
                
                # Si saldo llegó a 0, apagar breakers asociados
                try:
                    saldo_val = float(t.get('saldo') or 0.0)
                except Exception:
                    saldo_val = 0.0
                
                if saldo_val <= 0.0:
                    # Apagar breakers asociados a esta tarjeta
                    for br in breakers:
                        if br.get('tarjeta') == tarjeta_id and bool(br.get('estado')):
                            try:
                                await set_breaker(DATA_PATH, br.get('id'), False)
                                asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': br.get('id'), 'state': 'off'}))
                            except Exception as e:
                                errors.append({'breaker_id': br.get('id'), 'error': str(e)})
        except Exception as e:
            errors.append({'tarjeta_id': tarjeta_id, 'error': str(e)})
    
    return web.json_response({
        'ok': True,
        'processed': len(processed),
        'breakers': processed,
        'errors': errors
    })


async def ha_listener_forever():
    """Mantiene conexión WS con HA con reconexión automática y reenvía state_changed.

    Emite además eventos 'ha:status' con estados 'connected'/'disconnected'.
    """
    if not (HA_WS and HA_TOKEN):
        print('[HA Listener] NO INICIADO: HA_WS o HA_TOKEN no configurados')
        return
    print(f'[HA Listener] Iniciando conexión a {HA_WS[:50]}...')
    backoff = 3
    while True:
        try:
            async with websockets.connect(HA_WS, ping_interval=20, ping_timeout=20, max_queue=1000) as ws:
                # handshake
                hello = json.loads(await ws.recv())
                if hello.get('type') != 'auth_required':
                    print('[HA Listener] HA WS unexpected hello', hello)
                    await state.broadcast({'type': 'ha:status', 'status': 'disconnected', 'reason': 'unexpected_hello'})
                    raise RuntimeError('unexpected hello from HA')
                await ws.send(json.dumps({'type':'auth', 'access_token': HA_TOKEN}))
                resp = json.loads(await ws.recv())
                if resp.get('type') != 'auth_ok':
                    print('[HA Listener] HA WS auth failed', resp)
                    await state.broadcast({'type': 'ha:status', 'status': 'disconnected', 'reason': 'auth_failed'})
                    raise RuntimeError('auth failed to HA')
                # subscribe a state_changed
                msg = {'id': 1, 'type': 'subscribe_events', 'event_type': 'state_changed'}
                await ws.send(json.dumps(msg))
                ack = json.loads(await ws.recv())
                if not ack.get('success'):
                    print('[HA Listener] HA WS subscribe failed', ack)
                    await state.broadcast({'type': 'ha:status', 'status': 'disconnected', 'reason': 'subscribe_failed'})
                    raise RuntimeError('subscribe failed')
                print('[HA Listener] ✓ Conectado y suscrito a state_changed')
                await state.broadcast({'type': 'ha:status', 'status': 'connected'})
                backoff = 3  # resetear backoff al conectar
                async for raw in ws:
                    try:
                        evt = json.loads(raw)
                    except Exception:
                        continue
                    if evt.get('type') == 'event' and evt.get('event', {}).get('event_type') == 'state_changed':
                        data = evt['event']['data']
                        entity_id = data.get('entity_id')
                        new_state = data.get('new_state')
                        print(f'[HA Event] state_changed: entity_id={entity_id} state={new_state.get("state") if isinstance(new_state, dict) else new_state}')
                        # forward raw HA event to clients
                        asyncio.create_task(state.broadcast({'type':'ha:state_changed','entity_id':entity_id,'new_state':new_state}))
                        # if the new state corresponds to a breaker entity, update local models and notify clients
                        st = None
                        attrs = {}
                        if isinstance(new_state, dict):
                            st = new_state.get('state')
                            attrs = new_state.get('attributes') or {}
                        # only proceed if we have an entity_id and a state string
                        if entity_id:
                            models = load_models()
                            breakers = models.get('breakers', [])
                            # Log: buscar breaker matching - construir conjunto completo de entidades
                            matched = []
                            for b in breakers:
                                ids = set()
                                if b.get('entity_id'):
                                    ids.add(b.get('entity_id'))
                                extra = b.get('entities') or []
                                if isinstance(extra, list):
                                    for e in extra:
                                        if isinstance(e, str):
                                            ids.add(e)
                                for key in ('power_entity','energy_entity','voltage_entity','current_entity'):
                                    val = b.get(key)
                                    if isinstance(val, str):
                                        ids.add(val)
                                if entity_id in ids:
                                    matched.append(b)
                            
                            # Log resultado de búsqueda con valores recibidos
                            if matched:
                                b = matched[0]
                                # Mostrar valores recibidos para breaker 9 específicamente
                                if b.get('id') == 'eb9a238727302e4422hpdm':
                                    print(f'[HA Event] 🔍 BREAKER 9: entity_id={entity_id} state={st} attributes={attrs}')
                                print(f'[HA Event] ✓ Matched entity_id={entity_id} -> breaker id={b.get("id")} nombre={b.get("nombre")}')
                            else:
                                # No se encontró match - loguear solo switches/sensors sin asignar automáticamente
                                if entity_id.startswith(('switch.', 'sensor.')):
                                    # Mostrar breakers configurados para debugging
                                    breaker_info = [(b.get('id'), b.get('entity_id'), len(b.get('entities', []))) for b in breakers]
                                    print(f"[HA Event] ⚠️  Entidad no asociada: {entity_id} - Breakers: {breaker_info}")
                            for b in matched:
                                # actualizar estado solo si la entidad es principal o un switch
                                domain = entity_id.split('.',1)[0] if '.' in entity_id else ''
                                if st is not None and (entity_id == b.get('entity_id') or domain == 'switch'):
                                    new_state_bool = (st == 'on')
                                    if bool(b.get('estado')) != new_state_bool:
                                        set_breaker_state(DATA_PATH, b.get('id'), new_state_bool)
                                        asyncio.create_task(state.broadcast({'type':'breakers:update','id': b.get('id'),'state': 'on' if new_state_bool else 'off'}))
                                # métricas
                                # reutilizar extractor numeric
                                def extract_numeric(val):
                                    try:
                                        if val is None:
                                            return None
                                        if isinstance(val, (int, float)):
                                            return val
                                        return float(str(val))
                                    except Exception:
                                        return None

                                # detectar sufijos de métricas, incluyendo phase_a_*
                                metric_suffixes = {
                                    'power': ['power_entity', 'power', 'power_w', 'current_power_w'],
                                    'energy': ['energy_entity', 'energy', 'today_energy_kwh', 'energy_kwh'],
                                    'voltage': ['voltage_entity', 'voltage', 'voltage_v'],
                                    'current': ['current_entity', 'current', 'current_a'],
                                }

                                # añadimos heurísticas para phase_a_* que pueden venir como sensor.nombre_phase_a_current etc.
                                # si la entidad_id contiene 'phase_a' o endswith relevant suffixes intentamos mapearlo.
                                power = energy = voltage = current = None

                                # si el entity_id corresponde exactamente con alguna entidad configurada explícitamente
                                if entity_id == b.get('power_entity'):
                                    power = extract_numeric(st)
                                if entity_id == b.get('energy_entity'):
                                    energy = extract_numeric(st)
                                if entity_id == b.get('voltage_entity'):
                                    voltage = extract_numeric(st)
                                if entity_id == b.get('current_entity'):
                                    current = extract_numeric(st)

                                # si no hay coincidencia directa, intentar heurísticas con sufijos y palabras clave
                                # soporta tanto inglés (phase_a, current, voltage, power) como español (fase_a, corriente, tension, potencia)
                                try:
                                    lower_eid = entity_id.lower() if isinstance(entity_id, str) else ''
                                    # detectar corriente/current
                                    if current is None and ('corriente' in lower_eid or 'current' in lower_eid):
                                        current = extract_numeric(st)
                                    # detectar tensión/voltage
                                    if voltage is None and ('tension' in lower_eid or 'voltage' in lower_eid):
                                        voltage = extract_numeric(st)
                                    # detectar potencia/power
                                    if power is None and ('potencia' in lower_eid or 'power' in lower_eid):
                                        power = extract_numeric(st)
                                    # detectar energía/energy
                                    if energy is None and ('energia' in lower_eid or 'energy' in lower_eid):
                                        energy = extract_numeric(st)
                                except Exception:
                                    pass

                                # Caer a atributos genéricos si aún no tenemos valores
                                if power is None:
                                    power = extract_numeric(attrs.get('power') or attrs.get('current_power_w') or attrs.get('power_w'))
                                if energy is None:
                                    energy = extract_numeric(attrs.get('energy') or attrs.get('today_energy_kwh') or attrs.get('energy_kwh'))
                                if voltage is None:
                                    voltage = extract_numeric(attrs.get('voltage') or attrs.get('voltage_v'))
                                if current is None:
                                    current = extract_numeric(attrs.get('current') or attrs.get('current_a'))

                                fields = {}
                                if power is not None:
                                    fields['power'] = power
                                if energy is not None:
                                    fields['energy'] = energy
                                if voltage is not None:
                                    fields['voltage'] = voltage
                                if current is not None:
                                    fields['current'] = current
                                if fields:
                                    # Log detallado para breaker 9
                                    if b.get('id') == 'eb9a238727302e4422hpdm':
                                        print(f'[HA Event] 📊 BREAKER 9 actualizando: entity_id={entity_id} fields={fields}')
                                    update_breaker_fields(DATA_PATH, b.get('id'), **fields)
                                    asyncio.create_task(state.broadcast({'type':'breakers:consumption','id': b.get('id'), **fields}))
        except asyncio.CancelledError:
            # detener definitivamente
            print('[HA Listener] Detenido (cancelled)')
            await state.broadcast({'type': 'ha:status', 'status': 'disconnected', 'reason': 'cancelled'})
            break
        except Exception as e:
            # reconectar con backoff exponencial
            print(f'[HA Listener] ERROR, reconectando en {backoff}s: {e}')
            try:
                await state.broadcast({'type': 'ha:status', 'status': 'disconnected', 'error': str(e)[:200]})
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def make_app():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/display', display)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/models', models_handler)
    app.router.add_post('/rfid', rfid_post)
    app.router.add_post('/breakers/{id}/toggle', breaker_toggle_handler)
    app.router.add_post('/breakers/{id}/set', breaker_set_handler)
    app.router.add_post('/breakers/{id}/pulse', breaker_pulse_handler)
    app.router.add_post('/breakers/{id}/refresh', breaker_refresh_handler)
    app.router.add_get('/breakers/consumption', breakers_consumption_handler)
    app.router.add_post('/breakers/tick-consumption', breakers_tick_consumption_handler)
    # Rutas de uso/límites
    app.router.add_get('/usage-limits', usage_limits_handler)
    app.router.add_post('/usage-limits', usage_limits_update_handler)
    app.router.add_get('/breakers/{id}/usage', breaker_usage_handler)
    app.router.add_post('/breakers/{id}/usage', breaker_usage_update_handler)
    app.router.add_post('/breakers/{id}/usage/reset', breaker_usage_reset_handler)
    # Exponer endpoint para actualizar saldo de tarjetas desde la UI
    app.router.add_post('/tarjetas/{id}/saldo', tarjeta_update_saldo)
    # Ruta adicional para ajustar saldo (delta)
    app.router.add_post('/tarjetas/{id}/ajuste', tarjeta_adjust_saldo)
    # static
    static_dir = os.path.join(BASE_DIR, 'static')
    app.router.add_static('/static/', static_dir, show_index=True)
    # diagnostic: mostrar información sobre la configuración de HA (no imprimir token)
    try:
        import websockets as _ws_check
        ws_lib = 'websockets (imported ok)'
    except Exception as e:
        ws_lib = f'websockets (ERROR: {e})'
    print(f"[startup] HA_URL={'set: ' + HA_URL[:30] + '...' if HA_URL else 'NOT set'} HA_WS={'set' if HA_WS else 'NOT set'} HA_TOKEN={'set (len=' + str(len(HA_TOKEN)) + ')' if HA_TOKEN else 'NOT set'} websockets_lib={ws_lib}")
    print(f"[startup] Breakers configurados en data.json:")
    try:
        _temp_models = load_models()
        for _b in _temp_models.get('breakers', []):
            print(f"  - id={_b.get('id')} entity_id={_b.get('entity_id')} tuya_id={_b.get('tuya_id')} nombre={_b.get('nombre')}")
            # mostrar entidades configuradas para métricas
            has_metrics = any([_b.get('power_entity'), _b.get('voltage_entity'), _b.get('current_entity'), _b.get('energy_entity')])
            if has_metrics:
                print(f"    Métricas: power={_b.get('power_entity')} voltage={_b.get('voltage_entity')} current={_b.get('current_entity')} energy={_b.get('energy_entity')}")
            else:
                print(f"    ⚠️  Sin entidades de métricas configuradas explícitamente")
    except Exception as _e:
        print(f"  ERROR cargando breakers: {_e}")

    # start HA listener in the app event loop so it runs correctly
    if HA_WS and HA_TOKEN:
        async def _start_ha(app):
            app['ha_task'] = asyncio.create_task(ha_listener_forever())

        async def _stop_ha(app):
            t = app.get('ha_task')
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        app.on_startup.append(_start_ha)
        app.on_cleanup.append(_stop_ha)
    # always load models and start watcher
    async def _init_models(app):
        init_models_startup()
        app['watcher_task'] = asyncio.create_task(watch_data_file(app))

    # registrar init_models primero para que los demás startup hooks asuman modelos cargados
    app.on_startup.append(_init_models)

    # inicio del consumption manager (deducción por segundo)
    import importlib
    create_manager = None
    for mod_name in ('scripts.consumption_manager', 'consumption_manager', '.consumption_manager'):
        try:
            _mod = importlib.import_module(mod_name)
            create_manager = getattr(_mod, 'create_manager', None)
            # si el módulo tiene set_broadcaster, conectarlo para WS
            set_broadcaster = getattr(_mod, 'set_broadcaster', None)
            if set_broadcaster:
                # broadcaster usa el state.broadcast pero puede aceptar sync o async
                def _ws_emit(msg: dict):
                    return state.broadcast(msg)
                set_broadcaster(_ws_emit)
            if create_manager:
                break
        except Exception:
            continue

    # Permitir deshabilitar el consumo del lado servidor para evitar doble descuento con el tick del frontend
    enable_server_consumption = os.environ.get('ENABLE_SERVER_CONSUMPTION', '0') == '1'
    if create_manager and enable_server_consumption:
        async def _start_consumption(app):
            # _init_models fue añadido antes, on_startup mantiene el orden de append
            app['cons_mgr'] = create_manager(DATA_PATH)
            app['cons_mgr'].start()

        async def _stop_consumption(app):
            mgr = app.get('cons_mgr')
            if mgr:
                await mgr.stop()

        app.on_startup.append(_start_consumption)
        app.on_cleanup.append(_stop_consumption)

    async def _cleanup_models(app):
        t = app.get('watcher_task')
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    # sync inicial de breakers desde HA (estados y consumo)
    try:
        from .breaker_service import sync_all_breakers_from_ha
    except Exception:
        try:
            from breaker_service import sync_all_breakers_from_ha
        except Exception:
            sync_all_breakers_from_ha = None

    if HA_URL and HA_TOKEN:
        async def _sync_ha(app):
            if sync_all_breakers_from_ha:
                print('[Startup] Sincronizando breakers desde Home Assistant...')
                res = await sync_all_breakers_from_ha(DATA_PATH)
                if res.get('ok'):
                    updated = res.get('updated', [])
                    print(f'[Startup] ✓ Sincronizados {len(updated)} breakers desde HA')
                    # broadcast de cada breaker actualizado
                    for u in updated:
                        print(f"  - Breaker id={u['id']} estado={'on' if u.get('estado') else 'off'} power={u.get('power')} voltage={u.get('voltage')} current={u.get('current')}")
                        await state.broadcast({'type': 'breakers:update', 'id': u['id'], 'state': 'on' if u.get('estado') else 'off'})
                        m = {k:v for k,v in u.items() if k in ('power','energy','voltage','current') and v is not None}
                        if m:
                            m.update({'type':'breakers:consumption','id': u['id']})
                            await state.broadcast(m)
                else:
                    print(f'[Startup] ✗ Error en sync_all_breakers_from_ha: {res.get("error")}')
            else:
                print('[Startup] sync_all_breakers_from_ha no disponible')
        app.on_startup.append(_sync_ha)
    app.on_cleanup.append(_cleanup_models)
    # Ruta adicional para ajustar saldo (delta)
    app.router.add_post('/tarjetas/{id}/ajuste', tarjeta_adjust_saldo)
    return app


if __name__ == '__main__':
    host = os.environ.get('UI_HOST', '0.0.0.0')
    port = int(os.environ.get('UI_PORT', '9111'))
    web.run_app(make_app(), host=host, port=port)
