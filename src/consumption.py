import asyncio
import time
from typing import Callable, Dict, Any

from .storage import load_data, save_data, get_tarjeta
from .config import TICK_INTERVAL

# Heurística de normalización de potencia

def normalize_power(power, voltage, current):
    inferred = None
    try:
        if voltage is not None and current is not None:
            inferred = float(voltage) * float(current)
    except Exception:
        inferred = None
    try:
        if power is None:
            power = inferred
        else:
            power = float(power)
            if inferred is not None:
                if inferred > power * 10 and power < 10:
                    power = power * 1000.0
            else:
                if power < 10:
                    power = power * 1000.0
    except Exception:
        power = inferred
    return power


class ConsumptionLoop:
    def __init__(self, set_breaker_state: Callable[[str, bool], None]):
        self._running = False
        self._task: asyncio.Task | None = None
        self._set_breaker_state = set_breaker_state

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._running = False
        t = self._task
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _run(self):
        last = time.time()
        while self._running:
            now = time.time()
            elapsed = now - last
            last = now
            try:
                self.tick(elapsed)
            except Exception:
                pass
            await asyncio.sleep(TICK_INTERVAL)

    def tick(self, elapsed_seconds: float):
        data = load_data()
        changed = False
        # 1) Estaciones de carga: sumar saldo
        for ad in data.get('arduinos', []):
            if not ad.get('es_estacion_carga'):
                continue
            last = ad.get('last') or {}
            tarjeta_id = last.get('nfc') or last.get('rfid') or last.get('uid')
            if not tarjeta_id:
                continue
            wps = float(ad.get('w_por_segundo') or 0.0)
            if wps <= 0:
                continue
            t = get_tarjeta(data, tarjeta_id)
            if not t:
                continue
            t['saldo'] = round(float(t.get('saldo') or 0.0) + wps * elapsed_seconds, 6)
            changed = True
        # 2) Consumo por breakers
        for b in data.get('breakers', []):
            power = normalize_power(b.get('power'), b.get('voltage'), b.get('current'))
            if power is None:
                continue
            energy_ws = float(power) * float(elapsed_seconds)
            tarjeta_id = b.get('tarjeta')
            if not tarjeta_id:
                continue
            t = get_tarjeta(data, tarjeta_id)
            if not t:
                continue
            saldo = float(t.get('saldo') or 0.0)
            nuevo = round(max(0.0, saldo - energy_ws), 6)
            if nuevo != saldo:
                t['saldo'] = nuevo
                changed = True
            b['consumption_last_ws'] = round(energy_ws, 6)
            b['consumption_power_w'] = round(float(power), 6)
            if nuevo <= 0.0 and b.get('estado'):
                b['estado'] = False
                try:
                    self._set_breaker_state(b.get('id'), False)
                except Exception:
                    pass
                changed = True
        if changed:
            save_data(data)
