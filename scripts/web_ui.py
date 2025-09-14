#!/usr/bin/env python3
"""Servidor mínimo que expone únicamente el endpoint POST /rfid.

Este módulo no importa modelos ni registry. Solo recibe JSON/form con al menos
"uid" y opcionalmente "origen" y hace un log y devuelve el payload.
"""
import os
import json
import asyncio
from aiohttp import web
from typing import Set
import aiohttp
import websockets
import re
try:
    from .breaker_service import set_breaker, toggle_breaker_service, pulse_breaker_service
except Exception:
    from breaker_service import set_breaker, toggle_breaker_service, pulse_breaker_service

try:
    # cuando se ejecuta como paquete
    from .models_loader import get_models, get_breaker, toggle_breaker, set_breaker_state, get_tarjeta_for_breaker, update_breaker_fields
except Exception:
    try:
        # cuando se ejecuta como script desde la raíz del repo
        from scripts.models_loader import get_models, get_breaker, toggle_breaker, set_breaker_state, get_tarjeta_for_breaker, update_breaker_fields
    except Exception:
        # fallback: import directo si el script está en PYTHONPATH
        from models_loader import get_models, get_breaker, toggle_breaker, set_breaker_state, get_tarjeta_for_breaker, update_breaker_fields
# import tuya client separately (not nested inside models import) with fallbacks
try:
    from .tuya_client import perform_action as tuya_perform, perform_pulse as tuya_perform_pulse
except Exception:
    try:
        from scripts.tuya_client import perform_action as tuya_perform, perform_pulse as tuya_perform_pulse
    except Exception:
        try:
            from tuya_client import perform_action as tuya_perform, perform_pulse as tuya_perform_pulse
        except Exception:
            # safe fallback so server doesn't crash if tuya_client missing
            def tuya_perform(device_id: str, action: str):
                return False, 'tuya_client not available'
            def tuya_perform_pulse(device_id: str, duration_ms: int = 500):
                return False, 'tuya_client not available'

# NUEVO: cargar config central
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

# Usar config central (eliminar token hardcodeado aquí)
HA_URL = CFG_HA_URL
HA_TOKEN = CFG_HA_TOKEN
HA_WS  = os.getenv('HA_WS') or (HA_URL.replace('http','ws') + '/api/websocket')


class ServerState:
    def __init__(self):
        self.websockets: Set[web.WebSocketResponse] = set()

    async def broadcast(self, message: dict):
        text = json.dumps(message, ensure_ascii=False)
        to_remove = []
        for ws in list(self.websockets):
            try:
                await ws.send_str(text)
            except Exception:
                to_remove.append(ws)
        for ws in to_remove:
            self.websockets.discard(ws)


state = ServerState()


def load_models():
    try:
        with open(DATA_PATH, 'r', encoding='utf8') as f:
            return json.load(f)
    except Exception:
        return {"tarjetas": [], "breakers": [], "arduinos": []}


def save_models(models: dict):
    """Persistir models en DATA_PATH atomically."""
    try:
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
    """Vigila cambios de mtime en data.json y broadcast de modelos actualizados."""
    try:
        last_mtime = os.path.getmtime(DATA_PATH)
    except Exception:
        last_mtime = None
    print(f"[watcher] iniciado para {DATA_PATH}")
    while True:
        await asyncio.sleep(2)
        try:
            mtime = os.path.getmtime(DATA_PATH)
        except Exception:
            continue
        if last_mtime is None:
            last_mtime = mtime
            continue
        if mtime != last_mtime:
            last_mtime = mtime
            models = load_models()
            print(f"[watcher] cambio detectado en data.json -> broadcast models")
            await state.broadcast({'type': 'models', 'data': models})


async def index(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'static', 'index.html'))


async def models_handler(request):
    models = load_models()
    return web.json_response(models)


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    state.websockets.add(ws)

    # enviar modelos actuales al cliente
    models = load_models()
    await ws.send_str(json.dumps({"type": "models", "data": models}, ensure_ascii=False))
    await ws.send_str(json.dumps({"type": "info", "msg": "cliente conectado"}, ensure_ascii=False))

    try:
        async for msg in ws:
            # ignoramos mensajes del cliente por ahora
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

    uid = data.get('uid') or data.get('rfid')
    origen = data.get('origen') or data.get('arduino') or data.get('arduino_id')

    # log completo
    print('RFID received:', json.dumps(data, ensure_ascii=False))

    # broadcast a websockets
    # guardar ultimo dato en el arduino si existe
    if origen:
        try:
            models = load_models()
            arduinos = models.get('arduinos', [])
            matched = None
            for ad in arduinos:
                if ad.get('id') == origen:
                    matched = ad
                    break
            if matched is None:
                # intentar encontrar por campo 'arduino' en payload
                for ad in arduinos:
                    if ad.get('id') == data.get('arduino'):
                        matched = ad
                        break
            if matched is not None:
                matched['last'] = data
                # persistir y notificar cambio
                if save_models(models):
                    asyncio.create_task(state.broadcast({'type': 'arduinos:update', 'id': matched['id'], 'last': matched['last']}))
        except Exception as e:
            print('rfid_post save arduino error', e)

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
        models = load_models()
        tarjetas = models.get('tarjetas', [])
        t = next((x for x in tarjetas if x.get('id') == tid), None)
        if t is None:
            return web.json_response({'ok': False, 'error': 'unknown tarjeta'}, status=404)
        # intentar convertir a numero si viene como string
        try:
            nuevo = body.get('saldo')
            if isinstance(nuevo, str):
                if nuevo.strip() == '':
                    nuevo = None
                else:
                    nuevo = float(nuevo)
        except Exception:
            nuevo = body.get('saldo')
        t['saldo'] = nuevo
        # además de actualizar la tarjeta actualizar los breakers que referencien esta tarjeta
        try:
            for b in models.get('breakers', []):
                if b.get('tarjeta') == tid:
                    # si el saldo nuevo es numérico asignarlo a breaker.saldo y max_saldo
                    if isinstance(nuevo, (int, float)):
                        b['saldo'] = nuevo
                        b['max_saldo'] = nuevo
                    else:
                        # si viene None o string vacío eliminar campo saldo del breaker
                        b.pop('saldo', None)
                        b.pop('max_saldo', None)
        except Exception as e:
            print('tarjeta_update_saldo update breakers error', e)
        saved = save_models(models)
        if saved:
            # notificar a clientes con la tarjeta actualizada
            asyncio.create_task(state.broadcast({'type': 'tarjetas:update', 'id': tid, 'tarjeta': t}))
            # notificar updates en breakers asociados para refresco inmediato
            try:
                for b in models.get('breakers', []):
                    if b.get('tarjeta') == tid:
                        asyncio.create_task(state.broadcast({'type': 'breakers:update', 'id': b.get('id'), 'state': 'on' if b.get('estado') else 'off'}))
                        # también enviar consumo parcial si existe saldo/power
                        m = {k: v for k, v in b.items() if k in ('power', 'energy', 'voltage', 'current', 'saldo') and v is not None}
                        if m:
                            m.update({'type': 'breakers:consumption', 'id': b.get('id')})
                            asyncio.create_task(state.broadcast(m))
            except Exception:
                pass
            return web.json_response({'ok': True, 'tarjeta': t})
        else:
            return web.json_response({'ok': False, 'error': 'save failed'}, status=500)
    except Exception as e:
        print('tarjeta_update_saldo error', e)
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
    if svc_res.get('tuya') is not None:
        t = dict(svc_res.get('tuya', {}))
        if 'ok' in t:
            t['success'] = t.pop('ok')
        # pulse action name
        if 'action' not in t:
            t['action'] = 'pulse'
        payload = {'type': 'tuya:pulse', 'breaker_id': bid, **t}
        # include device identifier for UI
        if device_id:
            payload['device'] = device_id
        asyncio.create_task(state.broadcast(payload))
    return web.json_response({'ok': True, 'breaker_id': bid, 'tuya': svc_res.get('tuya')})


async def breakers_consumption_handler(request):
    """Devuelve métricas actuales de consumo de todos los breakers.

    Respuesta: { ok: true, breakers: [ {id, estado, power, energy, voltage, current} ] }
    """
    models = load_models()
    out = []
    for b in models.get('breakers', []):
        entry = {
            'id': b.get('id'),
            'estado': b.get('estado'),
            'power': b.get('power'),
            'energy': b.get('energy'),
            'voltage': b.get('voltage'),
            'current': b.get('current'),
        }
        out.append(entry)
        print(f"[consumption] breaker={entry['id']} estado={entry['estado']} power={entry['power']}W energy={entry['energy']}kWh voltage={entry['voltage']}V current={entry['current']}A")
    return web.json_response({'ok': True, 'breakers': out})


async def ha_listener():
    """Conecta al websocket de Home Assistant y re-broadcast eventos state_changed."""
    if not (HA_WS and HA_TOKEN):
        return
    try:
        async with websockets.connect(HA_WS) as ws:
            # handshake
            hello = json.loads(await ws.recv())
            if hello.get('type') != 'auth_required':
                print('HA WS unexpected hello', hello)
                return
            await ws.send(json.dumps({'type':'auth', 'access_token': HA_TOKEN}))
            resp = json.loads(await ws.recv())
            if resp.get('type') != 'auth_ok':
                print('HA WS auth failed', resp)
                return
            # subscribe
            msg = {'id': 1, 'type': 'subscribe_events', 'event_type': 'state_changed'}
            await ws.send(json.dumps(msg))
            ack = json.loads(await ws.recv())
            if not ack.get('success'):
                print('HA WS subscribe failed', ack)
                return
            print('HA listener connected')
            async for raw in ws:
                try:
                    evt = json.loads(raw)
                except Exception:
                    continue
                if evt.get('type') == 'event' and evt.get('event', {}).get('event_type') == 'state_changed':
                    data = evt['event']['data']
                    entity_id = data.get('entity_id')
                    new_state = data.get('new_state')
                    # forward raw HA event to clients
                    asyncio.create_task(state.broadcast({'type':'ha:state_changed','entity_id':entity_id,'new_state':new_state}))
                    # if the new state corresponds to a breaker entity, update local models and notify clients
                    try:
                        st = None
                        attrs = {}
                        if isinstance(new_state, dict):
                            st = new_state.get('state')
                            attrs = new_state.get('attributes') or {}
                        # only proceed if we have an entity_id and a state string
                        if entity_id:
                            models = load_models()
                            breakers = models.get('breakers', [])
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
                            if not matched:
                                # intentar heurística difusa: tokenizar entity_id y buscar correspondencia
                                try:
                                    pretty = {'entity_id': entity_id, 'state': st, 'attributes': attrs}
                                    print(f"[ha_listener] entidad sin breaker: {entity_id} -> {json.dumps(pretty, ensure_ascii=False)}")
                                except Exception:
                                    print(f"[ha_listener] entidad sin breaker: {entity_id}")
                                # fuzzy match: tomar la parte sin dominio y tokens
                                try:
                                    short = entity_id.split('.',1)[1] if '.' in entity_id else entity_id
                                    tokens = re.split('[._\-]', short.lower())
                                    def token_score(b):
                                        score = 0
                                        # nombre e id del breaker
                                        if b.get('id') and any(t in (b.get('id') or '').lower() for t in tokens):
                                            score += 2
                                        if b.get('nombre') and any(t in (b.get('nombre') or '').lower() for t in tokens):
                                            score += 2
                                        # entidades configuradas
                                        for key in ('entity_id','power_entity','energy_entity','voltage_entity','current_entity'):
                                            val = b.get(key)
                                            if isinstance(val, str) and any(t in val.lower() for t in tokens):
                                                score += 3
                                        # tarjeta id
                                        if b.get('tarjeta') and any(t in (b.get('tarjeta') or '').lower() for t in tokens):
                                            score += 1
                                        return score
                                    best = None
                                    best_score = 0
                                    for b in breakers:
                                        s = token_score(b)
                                        if s > best_score:
                                            best_score = s
                                            best = b
                                    if best and best_score >= 3:
                                        matched = [best]
                                        print(f"[ha_listener] fuzzy match: {entity_id} -> breaker {best.get('id')} (score={best_score})")
                                except Exception:
                                    pass
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

                                # si no hay coincidencia directa, intentar heurísticas con sufijos incluyendo phase_a
                                # ejemplo: sensor.agustin_phase_a_current -> mapear a current
                                try:
                                    lower_eid = entity_id.lower() if isinstance(entity_id, str) else ''
                                    if 'phase_a' in lower_eid:
                                        if lower_eid.endswith('_current') or '_current' in lower_eid:
                                            current = extract_numeric(st) if current is None else current
                                        if lower_eid.endswith('_voltage') or '_voltage' in lower_eid:
                                            voltage = extract_numeric(st) if voltage is None else voltage
                                        if lower_eid.endswith('_power') or '_power' in lower_eid:
                                            power = extract_numeric(st) if power is None else power
                                        if lower_eid.endswith('_energy') or '_energy' in lower_eid:
                                            energy = extract_numeric(st) if energy is None else energy
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
                                    update_breaker_fields(DATA_PATH, b.get('id'), **fields)
                                    asyncio.create_task(state.broadcast({'type':'breakers:consumption','id': b.get('id'), **fields}))
                    except Exception as e:
                        print('ha_listener update error', e)
    except Exception as e:
        print('ha_listener error', e)


def make_app():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/models', models_handler)
    app.router.add_post('/rfid', rfid_post)
    app.router.add_post('/breakers/{id}/toggle', breaker_toggle_handler)
    app.router.add_post('/breakers/{id}/set', breaker_set_handler)
    app.router.add_post('/breakers/{id}/pulse', breaker_pulse_handler)
    app.router.add_get('/breakers/consumption', breakers_consumption_handler)
    # Exponer endpoint para actualizar saldo de tarjetas desde la UI
    app.router.add_post('/tarjetas/{id}/saldo', tarjeta_update_saldo)
    # static
    static_dir = os.path.join(BASE_DIR, 'static')
    app.router.add_static('/static/', static_dir, show_index=True)
    # start HA listener in the app event loop so it runs correctly
    if HA_WS and HA_TOKEN:
        async def _start_ha(app):
            app['ha_task'] = asyncio.create_task(ha_listener())

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

    # inicio del consumption manager (deducción por segundo)
    try:
        from .consumption_manager import create_manager
    except Exception:
        try:
            from scripts.consumption_manager import create_manager
        except Exception:
            create_manager = None

    if create_manager:
        async def _start_consumption(app):
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

    app.on_startup.append(_init_models)
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
                res = await sync_all_breakers_from_ha(DATA_PATH)
                if res.get('ok'):
                    # broadcast de cada breaker actualizado
                    for u in res.get('updated', []):
                        await state.broadcast({'type': 'breakers:update', 'id': u['id'], 'state': 'on' if u.get('estado') else 'off'})
                        m = {k:v for k,v in u.items() if k in ('power','energy','voltage','current') and v is not None}
                        if m:
                            m.update({'type':'breakers:consumption','id': u['id']})
                            await state.broadcast(m)
                else:
                    print('sync_all_breakers_from_ha error', res.get('error'))
        app.on_startup.append(_sync_ha)
    app.on_cleanup.append(_cleanup_models)
    return app


if __name__ == '__main__':
    host = os.environ.get('UI_HOST', '0.0.0.0')
    port = int(os.environ.get('UI_PORT', '9111'))
    web.run_app(make_app(), host=host, port=port)
