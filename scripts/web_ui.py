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
try:
    from .breaker_service import set_breaker, toggle_breaker_service, pulse_breaker_service
except Exception:
    from breaker_service import set_breaker, toggle_breaker_service, pulse_breaker_service

try:
    # cuando se ejecuta como paquete
    from .models_loader import get_models, get_breaker, toggle_breaker, set_breaker_state, get_tarjeta_for_breaker
except Exception:
    try:
        # cuando se ejecuta como script desde la raíz del repo
        from scripts.models_loader import get_models, get_breaker, toggle_breaker, set_breaker_state, get_tarjeta_for_breaker
    except Exception:
        # fallback: import directo si el script está en PYTHONPATH
        from models_loader import get_models, get_breaker, toggle_breaker, set_breaker_state, get_tarjeta_for_breaker
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

API_KEY = os.environ.get('API_KEY')
BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, 'data.json')

# Home Assistant config (opcional)
HA_URL = os.getenv("HA_URL", "http://localhost:8123")  # Cambia a http://<tu-ip>:8123 si corres desde otra máquina
HA_WS  = os.getenv("HA_WS",  HA_URL.replace("http", "ws") + "/api/websocket")
HA_TOKEN = os.getenv("HA_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjZmIxZDYyNmVlODc0MjY2OTJhYjMwZmUxYmI0YTBhMiIsImlhdCI6MTc1NTY0MjU4OSwiZXhwIjoyMDcxMDAyNTg5fQ.v7yQXhrD41Xuba57UMRmcLtGtO6fSfEZLUT1QQ0kPN4")


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
                        if isinstance(new_state, dict):
                            st = new_state.get('state')
                        # only proceed if we have an entity_id and a state string
                        if entity_id and isinstance(st, str):
                            models = load_models()
                            for b in models.get('breakers', []):
                                if b.get('entity_id') == entity_id:
                                    # persist the breaker state (True if 'on')
                                    set_breaker_state(DATA_PATH, b.get('id'), True if st == 'on' else False)
                                    # notify clients that breaker changed
                                    asyncio.create_task(state.broadcast({'type':'breakers:update','id': b.get('id'),'state': 'on' if st=='on' else 'off'}))
                                    break
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
    return app


if __name__ == '__main__':
    host = os.environ.get('UI_HOST', '0.0.0.0')
    port = int(os.environ.get('UI_PORT', '9111'))
    web.run_app(make_app(), host=host, port=port)
