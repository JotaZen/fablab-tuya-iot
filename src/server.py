import asyncio
import json
import os
from aiohttp import web

from .storage import load_data, save_data
from .config import UI_HOST, UI_PORT
from .consumption import ConsumptionLoop

# Simple setters: persist estado en data.json

def set_breaker_state_local(breaker_id: str, state: bool):
    data = load_data()
    for b in data.get('breakers', []):
        if b.get('id') == breaker_id:
            b['estado'] = bool(state)
            break
    save_data(data)


def make_app():
    app = web.Application()

    loop = ConsumptionLoop(set_breaker_state_local)

    async def on_startup(app):
        # ensure data file exists
        data = load_data()
        if data is None:
            save_data({"tarjetas": [], "breakers": [], "arduinos": []})
        loop.start()

    async def on_cleanup(app):
        await loop.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    async def index(_):
        html = """
        <html>
          <head><title>Tuya Local</title></head>
          <body>
            <h3>Tuya Local</h3>
            <p>Endpoints: GET /models, POST /rfid, POST /tarjetas/{id}/saldo</p>
          </body>
        </html>"""
        return web.Response(text=html, content_type='text/html')

    async def get_models(_):
        return web.json_response(load_data())

    async def post_rfid(request: web.Request):
        try:
            body = await request.json()
        except Exception:
            body = dict(await request.post())
        data = load_data()
        origen = body.get('id') or body.get('origen')
        uid = body.get('nfc') or body.get('rfid') or body.get('uid') or body.get('card') or body.get('tag')
        if origen:
            for ad in data.get('arduinos', []):
                if ad.get('id') == origen:
                    ad['last'] = body
                    break
        save_data(data)
        return web.json_response({'ok': True, 'received': body, 'uid': uid, 'origen': origen})

    async def tarjeta_saldo(request: web.Request):
        tid = request.match_info.get('id')
        try:
            body = await request.json()
        except Exception:
            body = dict(await request.post())
        if 'saldo' not in body:
            return web.json_response({'ok': False, 'error': 'missing saldo'}, status=400)
        data = load_data()
        for t in data.get('tarjetas', []):
            if t.get('id') == tid:
                try:
                    t['saldo'] = float(body.get('saldo'))
                except Exception:
                    t['saldo'] = body.get('saldo')
                break
        save_data(data)
        return web.json_response({'ok': True})

    app.router.add_get('/', index)
    app.router.add_get('/models', get_models)
    app.router.add_post('/rfid', post_rfid)
    app.router.add_post('/tarjetas/{id}/saldo', tarjeta_saldo)

    return app


def run():
    web.run_app(make_app(), host=UI_HOST, port=UI_PORT)
