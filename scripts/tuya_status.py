import os
import json
import asyncio
import aiohttp
import websockets

# ----------------------------
# Config
# ----------------------------
HA_URL = os.getenv("HA_URL", "http://localhost:8123")  # Cambia a http://<tu-ip>:8123 si corres desde otra máquina
HA_WS  = os.getenv("HA_WS",  HA_URL.replace("http", "ws") + "/api/websocket")
HA_TOKEN = os.getenv("HA_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJjZmIxZDYyNmVlODc0MjY2OTJhYjMwZmUxYmI0YTBhMiIsImlhdCI6MTc1NTY0MjU4OSwiZXhwIjoyMDcxMDAyNTg5fQ.v7yQXhrD41Xuba57UMRmcLtGtO6fSfEZLUT1QQ0kPN4")

# ----------------------------
# REST helpers (GET s  tes, call services)
# ----------------------------
async def rest_get_states():
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{HA_URL}/api/states", headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

async def rest_call_service(domain: str, service: str, data: dict):
    """Ej: rest_call_service('switch', 'turn_on', {'entity_id': 'switch.tuya_plug_1'})"""
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    url = f"{HA_URL}/api/services/{domain}/{service}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=json.dumps(data)) as resp:
            resp.raise_for_status()
            return await resp.json()

# ----------------------------
# WebSocket helpers (tiempo real)
# ----------------------------
class HAWebSocketClient:
    def __init__(self, ws_url: str, token: str):
        self.ws_url = ws_url
        self.token = token
        self._msg_id = 1
        self.ws = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, ping_interval=5, ping_timeout=20)
        # handshake
        hello = json.loads(await self.ws.recv())  # 'auth_required'
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"WS inesperado: {hello}")
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth_ok = json.loads(await self.ws.recv())
        if auth_ok.get("type") != "auth_ok":
            raise RuntimeError(f"Auth WS falló: {auth_ok}")
        print("✅ WebSocket autenticado.")

    async def subscribe_state_changed(self):
        msg = {"id": self._next_id(), "type": "subscribe_events", "event_type": "state_changed"}
        await self.ws.send(json.dumps(msg))
        ack = json.loads(await self.ws.recv())
        if ack.get("type") != "result" or not ack.get("success"):
            raise RuntimeError(f"No se pudo suscribir: {ack}")
        print("🔔 Suscrito a eventos state_changed.")

    async def call_service(self, domain: str, service: str, service_data: dict):
        msg = {
            "id": self._next_id(),
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": service_data,
        }
        await self.ws.send(json.dumps(msg))
        resp = json.loads(await self.ws.recv())
        if resp.get("type") != "result" or not resp.get("success"):
            raise RuntimeError(f"call_service falló: {resp}")
        return resp

    async def listen_forever(self):
        try:
            async for raw in self.ws:
                evt = json.loads(raw)
                if evt.get("type") == "event" and evt.get("event", {}).get("event_type") == "state_changed":
                    entity_id = evt["event"]["data"]["entity_id"]
                    new_state = evt["event"]["data"]["new_state"]
                    state = new_state.get("state") if new_state else None
                    print(f"🛰  {entity_id} → {state}")
        except websockets.ConnectionClosed:
            print("WS cerrado.")

    async def close(self):
        if self.ws:
            await self.ws.close()

# ----------------------------
# Demo / main
# ----------------------------
async def main():
    if not HA_TOKEN or HA_TOKEN.startswith("<PEGA_"):
        raise SystemExit("Falta HA_TOKEN. Ponlo en el script o exporta HA_TOKEN en el entorno.")

    print(f"Conectando a {HA_URL}…")

    # 1) REST: listar estados
    states = await rest_get_states()
    print(f"🔎 Estados recibidos: {len(states)} (mostrando 3)")
    for s in states[:3]:
        print("-", s.get("entity_id"), "=", s.get("state"))

    # 2) REST: encender un switch (ajusta tu entity_id)
    # await rest_call_service("switch", "turn_on", {"entity_id": "switch.tuya_plug_1"})

    # 3) WS: conectar, suscribirse y (opcional) llamar servicios en tiempo real
    client = HAWebSocketClient(HA_WS, HA_TOKEN)
    await client.connect()
    await client.subscribe_state_changed()

    # Ejemplo: alternar un switch Tuya local (ajusta el entity_id a uno real tuyo)
    # await client.call_service("switch", "toggle", {"entity_id": "switch.tuya_plug_1"})
    print("Escuchando cambios de estado (Ctrl+C para salir)…")
    await client.listen_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
