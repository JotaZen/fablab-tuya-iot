"""Módulo que administra consumo por breaker y deducción de saldo.

Responsabilidades:
- Mantener un bucle asíncrono que cada segundo calcula consumo por breaker a partir de `power` (W)
  y lo transforma a energía (kWh) por segundo: energy_delta_kwh = power_W / 3600000
- Restar del `tarjeta.saldo` el coste/consumo (en las mismas unidades que `saldo`).
- Si el saldo llega a 0 o negativo, solicitar apagado del breaker mediante `toggle_breaker` o `set_breaker_state`.
- Solo responsabilidad del manager: cambiar campos `consumption_last` y `power_consuming` y persistir vía `save_data`.

Buenas prácticas:
- Separación: este módulo no conoce detalles de Tuya/HA; usa la API de `models_loader`.
- Configurable: INTERVAL_SECONDS constante.
"""
from typing import Dict, Any
import asyncio
import time
import logging
import importlib

log = logging.getLogger('consumption_manager')

models_mod = importlib.import_module('scripts.models_loader')
load_data = getattr(models_mod, 'load_data')
save_data = getattr(models_mod, 'save_data')
update_breaker_fields = getattr(models_mod, 'update_breaker_fields')
get_tarjeta_for_breaker = getattr(models_mod, 'get_tarjeta_for_breaker')
set_breaker_state = getattr(models_mod, 'set_breaker_state')

INTERVAL_SECONDS = 1.0

class ConsumptionManager:
    def __init__(self, path: str):
        self.path = path
        self._task = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info('consumption_manager started')

    async def stop(self):
        if not self._running:
            return
        self._running = False
        t = self._task
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info('consumption_manager stopped')

    async def _loop(self):
        # mantiene un timestamp para integracion
        last = time.time()
        try:
            while self._running:
                now = time.time()
                elapsed = now - last
                last = now
                try:
                    self._tick(elapsed)
                except Exception as e:
                    log.exception('tick error')
                await asyncio.sleep(INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return

    def _tick(self, elapsed_seconds: float):
        data = load_data(self.path)
        changed = False
        for b in data.get('breakers', []):
            # si no hay power medido, saltar
            power = b.get('power')
            if power is None:
                continue
            # power W -> energy kWh por elapsed: W * s / 3600000
            energy_kwh = (power * elapsed_seconds) / 3600000.0
            # restar del saldo asociado
            tarjeta = get_tarjeta_for_breaker(self.path, b)
            if tarjeta is None:
                continue
            saldo = tarjeta.get('saldo') or 0.0
            # suponemos que saldo está en la misma unidad que energía (kWh) o en moneda?
            # DECISIÓN: interpretamos `saldo` como kWh disponible (simplifica demo). Si tu sistema usa moneda,
            # reemplazar la conversión por coste monetario.
            nuevo_saldo = round(float(saldo) - energy_kwh, 6)
            if nuevo_saldo != saldo:
                # actualizar tarjeta en el objeto global
                for t in data.get('tarjetas', []):
                    if t.get('id') == tarjeta.get('id'):
                        t['saldo'] = nuevo_saldo
                        changed = True
                        break
            # marcar consumo actual
            b['consumption_last_kwh'] = round(energy_kwh, 8)
            b['consumption_power_w'] = power
            # si saldo agotado, apagar breaker
            if nuevo_saldo <= 0 and b.get('estado'):
                # persistimos antes de llamar al servicio externo
                save_data(self.path, data)
                # set_breaker_state cambiará estado y hará acciones tuya/ha
                set_breaker_state(self.path, b.get('id'), False)
                changed = True
        if changed:
            save_data(self.path, data)


# helper factory
_manager = None

def create_manager(path: str) -> ConsumptionManager:
    global _manager
    if _manager is None:
        _manager = ConsumptionManager(path)
    return _manager
