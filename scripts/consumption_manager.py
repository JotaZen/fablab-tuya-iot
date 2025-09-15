"""Módulo que administra consumo por breaker y deducción de saldo.

Ahora: deduce saldo en unidades de W·s (watts por segundo), es decir por cada segundo
se resta `power_W * elapsed_seconds`. Cuando el saldo llega a 0 o menos, se apaga el
breaker llamando a `set_breaker_state`.

Responsabilidades:
- Mantener un bucle asíncrono que cada INTERVAL_SECONDS calcula el consumo desde el
    campo `power` (W) y lo transforma a energía en W·s: energy_delta_ws = power_W * elapsed_seconds
- Restar del `tarjeta.saldo` esta cantidad (se asume que `saldo` está representado en W·s).
- Si el saldo llega a 0 o negativo, solicitar apagado del breaker mediante `set_breaker_state`.
"""
from typing import Dict, Any, Callable
import asyncio
import time
import logging
import importlib

log = logging.getLogger('consumption_manager')
# Configuración de logging por defecto (solo si no hay handlers configurados)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )

models_mod = importlib.import_module('scripts.models_loader')
load_data = getattr(models_mod, 'load_data')
save_data = getattr(models_mod, 'save_data')
update_breaker_fields = getattr(models_mod, 'update_breaker_fields')
get_tarjeta_for_breaker = getattr(models_mod, 'get_tarjeta_for_breaker')
set_breaker_state = getattr(models_mod, 'set_breaker_state')
adjust_tarjeta_saldo = getattr(models_mod, 'adjust_tarjeta_saldo', None)
# importar clase Tarjeta para callbacks
models_py = importlib.import_module('scripts.models')
TarjetaClass = getattr(models_py, 'Tarjeta')
try:
    bs_mod = importlib.import_module('scripts.breaker_service')
    async_set_breaker = getattr(bs_mod, 'set_breaker', None)
except Exception:
    async_set_breaker = None

# Broadcaster global (inyectado por web_ui). Debe estar disponible SIEMPRE.
_broadcaster: Callable[[dict], Any] | None = None

def set_broadcaster(cb: Callable[[dict], Any]):
    """Registrar una función (sincrónica o async) para emitir eventos a la UI.

    La función recibirá un diccionario con el payload a enviar por WebSocket.
    """
    global _broadcaster
    _broadcaster = cb

def _emit(msg: dict):
    """Intentar emitir un mensaje usando el broadcaster si está disponible."""
    global _broadcaster
    if _broadcaster is None:
        return
    try:
        res = _broadcaster(msg)
        # si es coroutine, agendar
        if asyncio.iscoroutine(res):
            asyncio.create_task(res)
    except Exception:
        # no romper el loop por errores de UI
        pass


def _strip_breaker_saldo(data: Dict[str, Any]) -> None:
    """Eliminar campos `saldo` y `max_saldo` de los breakers."""
    try:
        for bb in data.get('breakers', []):
            if 'saldo' in bb:
                bb.pop('saldo', None)
            if 'max_saldo' in bb:
                bb.pop('max_saldo', None)
    except Exception:
        pass

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
            # solo descontar si el breaker está encendido
            if not bool(b.get('estado')):
                continue
            # obtener potencia reportada
            power = b.get('power')
            v = b.get('voltage')
            i = b.get('current')
            # intentar inferir potencia desde voltage * current si está disponible
            inferred = None
            try:
                if v is not None and i is not None:
                    inferred = float(v) * float(i)
            except Exception:
                inferred = None

            # Normalizar power a vatios. Algunos sensores/reportes usan kW (p.ej. 0.059)
            # o ya vienen en W (p.ej. 59). Regla heurística:
            # - Si power es None, usar inferred si existe.
            # - Si power < 10 y inferred is not None and inferred > power * 10, es probable que power esté en kW -> convertir a W (power*1000).
            # - Si power < 10 y inferred is None, también puede ser kW: convertir a W si parece razonable (multiplicar por 1000).
            try:
                if power is None:
                    power = inferred
                else:
                    power = float(power)
                    if inferred is not None:
                        # si la inferred es mucho mayor que power, ajustar escala
                        if inferred > power * 10 and power < 10:
                            # probable kW -> pasar a W
                            power = power * 1000.0
                    else:
                        # no hay inferred; si power es pequeño (<10) asumimos kW
                        if power < 10:
                            power = power * 1000.0
            except Exception:
                power = inferred

            if power is None:
                # no hay forma de saber la potencia
                try:
                    log.debug(f"[tick consumo] breaker={b.get('id')} sin potencia disponible (no se descuenta)")
                except Exception:
                    pass
                # también enviar un ping de consumo sin potencia para trazabilidad en UI
                try:
                    _emit({'type': 'breakers:consumption', 'id': b.get('id'), 'power': None, 'ws': 0.0})
                except Exception:
                    pass
                continue

            # restar del saldo asociado usando helper centralizado
            tarjeta = get_tarjeta_for_breaker(self.path, b)
            if tarjeta is None:
                continue
            saldo = tarjeta.get('saldo') or 0.0
            nuevo_saldo = float(saldo)
            # Si el saldo ya es 0 o menos y el breaker está ON, apagarlo de inmediato
            if nuevo_saldo <= 0.0 and b.get('estado'):
                try:
                    b['estado'] = False
                    _strip_breaker_saldo(data)
                    save_data(self.path, data)
                except Exception:
                    pass
                try:
                    if async_set_breaker is not None:
                        # ejecutar apagado físico vía servicio asíncrono (Tuya/HA)
                        asyncio.create_task(async_set_breaker(self.path, b.get('id'), False))
                except Exception:
                    log.exception('error scheduling async_set_breaker OFF')
                try:
                    log.info(f"[tick consumo] breaker={b.get('id')} tarjeta={tarjeta.get('id')} saldo=0 -> forzar OFF")
                except Exception:
                    pass
                # avisar a la UI
                try:
                    _emit({'type': 'breakers:update', 'id': b.get('id'), 'state': 'off', 'reason': 'saldo=0'})
                except Exception:
                    pass
                changed = True
                # nada más que hacer para este breaker en este tick
                continue

            # ahora power está en W. energy en W·s por elapsed: W * s
            energy_ws = (float(power) * elapsed_seconds)
            try:
                log.debug(f"tick breaker={b.get('id')} powerW={power:.3f} elapsed={elapsed_seconds:.3f}s energyWs={energy_ws:.3f} tarjeta={tarjeta.get('id')} saldo_before={saldo}")
            except Exception:
                pass
            if adjust_tarjeta_saldo is not None:
                # delta negativo
                t_upd = adjust_tarjeta_saldo(self.path, tarjeta.get('id'), -energy_ws)
                if t_upd is not None:
                    try:
                        nuevo_saldo = float(t_upd.get('saldo') or 0.0)
                    except Exception:
                        nuevo_saldo = 0.0
                    # recargar data tras persistencia para mantener consistencia
                    data = load_data(self.path)
                    # re-vincular 'b' al breaker dentro del nuevo data
                    try:
                        bid = b.get('id')
                        for bb in data.get('breakers', []):
                            if bb.get('id') == bid:
                                b = bb
                                break
                    except Exception:
                        pass
                try:
                    log.info(
                        f"[tick consumo] breaker={b.get('id')} tarjeta={tarjeta.get('id')} "
                        f"W={float(power):.2f} Ws={energy_ws:.2f} saldo {float(saldo):.2f} -> {float(nuevo_saldo):.2f}"
                    )
                    _emit({'type': 'breakers:consumption', 'id': b.get('id'), 'power': round(float(power), 6), 'ws': round(energy_ws, 6), 'tarjeta': tarjeta.get('id'), 'saldo_before': float(saldo), 'saldo_after': float(nuevo_saldo)})
                except Exception:
                    pass
                changed = True
            else:
                # Fallback con clase Tarjeta si helper no disponible
                tmp_tar = TarjetaClass(tarjeta.get('id'))
                tmp_tar.saldo = float(saldo)
                def _on_empty_closure(tid=tarjeta.get('id')):
                    try:
                        for bb in data.get('breakers', []):
                            if bb.get('tarjeta') == tid and bb.get('estado'):
                                bb['estado'] = False
                                try:
                                    set_breaker_state(self.path, bb.get('id'), False)
                                except Exception:
                                    log.exception('on_empty: set_breaker_state error')
                    except Exception:
                        log.exception('on_empty closure error')
                tmp_tar.on_empty = _on_empty_closure
                tmp_tar.consumir(energy_ws)
                nuevo_saldo = round(float(tmp_tar.saldo), 6)
                if nuevo_saldo != float(saldo):
                    for t in data.get('tarjetas', []):
                        if t.get('id') == tarjeta.get('id'):
                            t['saldo'] = nuevo_saldo
                            changed = True
                            break
                try:
                    log.info(
                        f"[tick consumo] (fallback) breaker={b.get('id')} tarjeta={tarjeta.get('id')} "
                        f"W={float(power):.2f} Ws={energy_ws:.2f} saldo {float(saldo):.2f} -> {float(nuevo_saldo):.2f}"
                    )
                    _emit({'type': 'breakers:consumption', 'id': b.get('id'), 'power': round(float(power), 6), 'ws': round(energy_ws, 6), 'tarjeta': tarjeta.get('id'), 'saldo_before': float(saldo), 'saldo_after': float(nuevo_saldo)})
                except Exception:
                    pass
            # marcar consumo actual (en W·s por intervalo) y potencia
            b['consumption_last_ws'] = round(energy_ws, 6)
            b['consumption_power_w'] = power
            changed = True
            # si saldo agotado, apagar breaker (si está encendido).
            # Nota: adjust_tarjeta_saldo ya intenta sincronizar breakers según saldo,
            # pero reforzamos aquí por seguridad si el estado quedó inconsistente.
            if nuevo_saldo <= 0 and b.get('estado'):
                try:
                    # reflejar en archivo y disparar apagado físico
                    b['estado'] = False
                    _strip_breaker_saldo(data)
                    save_data(self.path, data)
                    if async_set_breaker is not None:
                        asyncio.create_task(async_set_breaker(self.path, b.get('id'), False))
                    # reload to reflect external change
                    data = load_data(self.path)
                except Exception:
                    log.exception('error apagando breaker tras saldo agotado')
                try:
                    log.warning(f"saldo agotado -> breaker OFF id={b.get('id')} tarjeta={tarjeta.get('id')}")
                except Exception:
                    pass
                try:
                    _emit({'type': 'breakers:update', 'id': b.get('id'), 'state': 'off', 'reason': 'saldo agotado'})
                except Exception:
                    pass
                changed = True
        if changed:
            _strip_breaker_saldo(data)
            save_data(self.path, data)


# helper factory
_manager = None

def create_manager(path: str) -> ConsumptionManager:
    global _manager
    if _manager is None:
        _manager = ConsumptionManager(path)
    return _manager
