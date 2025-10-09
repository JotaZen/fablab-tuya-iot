import os
import asyncio
import json
import base64
import re
from typing import Optional, Dict, Any, List
import importlib

# robust import for models_loader: try several module names depending on execution context
models_mod = None
for _name in ('models_loader', 'scripts.models_loader', '.models_loader'):
    try:
        models_mod = importlib.import_module(_name)
        break
    except Exception:
        models_mod = None

if not models_mod:
    # try loading from file path as last resort (works when running as script)
    try:
        from importlib.machinery import SourceFileLoader
        import pathlib
        base = pathlib.Path(__file__).resolve().parent
        mod_path = str(base.joinpath('models_loader.py'))
        models_mod = SourceFileLoader('models_loader', mod_path).load_module()
    except Exception as e:
        raise ImportError('could not import models_loader (tried models_loader, scripts.models_loader, .models_loader)') from e

get_breaker = getattr(models_mod, 'get_breaker')
set_breaker_state = getattr(models_mod, 'set_breaker_state')
toggle_breaker = getattr(models_mod, 'toggle_breaker')
load_data = getattr(models_mod, 'load_data')
save_data = getattr(models_mod, 'save_data')
update_breaker_fields = getattr(models_mod, 'update_breaker_fields', None)

# tuya client fallbacks
try:
    from .tuya_client import perform_action as _tuya_action, perform_pulse as _tuya_pulse
except Exception:
    try:
        from scripts.tuya_client import perform_action as _tuya_action, perform_pulse as _tuya_pulse
    except Exception:
        try:
            from tuya_client import perform_action as _tuya_action, perform_pulse as _tuya_pulse
        except Exception:
            def _tuya_action(device_id: str, action: str):
                return False, 'tuya_client not available'
            def _tuya_pulse(device_id: str, duration_ms: int = 500):
                return False, 'tuya_client not available'

try:
    from .config import HA_URL, HA_TOKEN
except Exception:
    try:
        from config import HA_URL, HA_TOKEN
    except Exception:
        HA_URL = os.getenv('HA_URL')
        HA_TOKEN = os.getenv('HA_TOKEN')


async def _call_ha_service(entity_id: str, svc: str) -> Dict[str, Any]:
    """Call Home Assistant switch service and return result dict."""
    if not (HA_URL and HA_TOKEN and entity_id):
        return {'ok': False, 'error': 'ha_not_configured_or_entity_missing'}
    url = f"{HA_URL}/api/services/switch/{svc}"
    headers = {'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'}
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json={'entity_id': entity_id}) as resp:
                try:
                    j = await resp.json()
                except Exception:
                    j = {'status': resp.status}
                return {'ok': True, 'result': j}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


async def _run_tuya_action(device_id: str, action: str) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    try:
        ok, msg = await loop.run_in_executor(None, lambda: _tuya_action(device_id or '', action))
        return {'success': bool(ok), 'msg': msg, 'action': action}
    except Exception as e:
        return {'success': False, 'msg': str(e), 'action': action}


async def _run_tuya_pulse(device_id: str, duration_ms: int = 500) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    try:
        ok, msg = await loop.run_in_executor(None, lambda: _tuya_pulse(device_id or '', duration_ms))
        return {'success': bool(ok), 'msg': msg, 'action': 'pulse'}
    except Exception as e:
        return {'success': False, 'msg': str(e), 'action': 'pulse'}


async def set_breaker(path: str, breaker_id: str, state: bool) -> Dict[str, Any]:
    """Persist state, then attempt Tuya and HA actions. Returns a summary dict."""
    br = set_breaker_state(path, breaker_id, state)
    if not br:
        return {'ok': False, 'error': 'unknown_breaker'}

    # prepare results
    res = {'ok': True, 'breaker': br, 'tuya': None, 'ha': None}

    # Priorizar entity_id si parece una entidad de HA (contiene punto)
    # Esto permite que el control vía HA funcione correctamente desde la UI
    entity_id = br.get('entity_id')
    tuya_id = br.get('tuya_id')
    nombre = br.get('nombre', 'SIN_NOMBRE')
    
    if entity_id and '.' in str(entity_id):
        device_id = entity_id
        print(f"🔧 set_breaker [{nombre}]: ID={breaker_id}, entity_id={entity_id}, tuya_id={tuya_id}, state={'ON' if state else 'OFF'}")
    else:
        device_id = (br.get('device_id') or br.get('tuya_device') or br.get('tuya_id') or br.get('tuya') or entity_id)
        print(f"🔧 set_breaker [{nombre}]: ID={breaker_id}, device_id={device_id}, tuya_id={tuya_id}, state={'ON' if state else 'OFF'} (not HA entity)")
    
    if not device_id:
        print(f"⚠️ set_breaker [{nombre}]: NO device identifier found! breaker_id={breaker_id}, keys={list(br.keys())}")
    
    action = 'encender' if state else 'apagar'
    tuya_res = await _run_tuya_action(device_id or '', action)
    # normalize older 'ok' key if present
    if isinstance(tuya_res, dict) and 'ok' in tuya_res:
        tuya_res['success'] = bool(tuya_res.pop('ok'))
    tuya_res.setdefault('action', action)
    res['tuya'] = tuya_res
    
    # Log del resultado de Tuya
    if tuya_res.get('success'):
        print(f"   ✅ Tuya response OK para {nombre}: {tuya_res.get('msg', 'success')}")
    else:
        print(f"   ❌ Tuya response FAILED para {nombre}: {tuya_res.get('msg', 'unknown error')}")

    # home assistant
    entity = br.get('entity_id')
    if entity and HA_URL and HA_TOKEN:
        svc = 'turn_on' if state else 'turn_off'
        ha_res = await _call_ha_service(entity, svc)
        res['ha'] = ha_res
        
        # Log del resultado de HA
        if ha_res.get('ok'):
            print(f"   ✅ Home Assistant response OK para {nombre}: service={svc}, entity={entity}")
        else:
            print(f"   ❌ Home Assistant response FAILED para {nombre}: {ha_res.get('error', 'unknown error')}")

    # Si encendemos el breaker, inicializar su saldo desde la tarjeta asociada (si existe)
    if state:
        try:
            tarjeta = get_tarjeta_for_breaker(path, br)
            if tarjeta and 'saldo' in tarjeta:
                # persistir saldo y max_saldo en el breaker
                if update_breaker_fields:
                    updated = update_breaker_fields(path, breaker_id, saldo=tarjeta.get('saldo'), max_saldo=tarjeta.get('saldo'))
                    if updated is not None:
                        res['breaker'] = updated
        except Exception:
            pass

    return res


async def toggle_breaker_service(path: str, breaker_id: str) -> Dict[str, Any]:
    # read current breaker and flip state explicitly to avoid ambiguity across imports
    br = get_breaker(path, breaker_id)
    if not br:
        return {'ok': False, 'error': 'unknown_breaker'}
    current = bool(br.get('estado', False))
    new_state = not current
    # persist
    updated = set_breaker_state(path, breaker_id, new_state)
    if not updated:
        return {'ok': False, 'error': 'failed_to_persist'}
    # Priorizar entity_id si es una entidad de HA válida
    entity_id = updated.get('entity_id')
    if entity_id and '.' in str(entity_id):
        device_for_tuya = entity_id
        print(f"breaker_service.toggle: using HA entity_id={entity_id} for breaker {breaker_id} -> {new_state}")
    else:
        device_for_tuya = (updated.get('device_id') or updated.get('tuya_device') or updated.get('tuya_id') or updated.get('tuya') or entity_id or '')
        print(f"breaker_service.toggle: using device_for_tuya={device_for_tuya} (not HA entity) for breaker {breaker_id} -> {new_state}")
    tuya = await _run_tuya_action(device_for_tuya, 'encender' if new_state else 'apagar')
    if isinstance(tuya, dict) and 'ok' in tuya:
        tuya['success'] = bool(tuya.pop('ok'))
    tuya.setdefault('action', 'toggle')
    ha = None
    if updated.get('entity_id') and HA_URL and HA_TOKEN:
        svc = 'turn_on' if new_state else 'turn_off'
        ha = await _call_ha_service(updated.get('entity_id'), svc)
    # Si encendemos por toggle, inicializar saldo desde la tarjeta asociada
    if new_state:
        try:
            tarjeta = get_tarjeta_for_breaker(path, updated)
            if tarjeta and 'saldo' in tarjeta and update_breaker_fields:
                updated2 = update_breaker_fields(path, breaker_id, saldo=tarjeta.get('saldo'), max_saldo=tarjeta.get('saldo'))
                if updated2 is not None:
                    updated = updated2
        except Exception:
            pass
    # debug
    print(f"toggle_breaker_service: breaker={breaker_id} {current} -> {new_state} tuya_success={tuya.get('success')} msg={tuya.get('msg')}")
    return {'ok': True, 'breaker': updated, 'tuya': tuya, 'ha': ha}


async def pulse_breaker_service(path: str, breaker_id: str, duration_ms: int = 500) -> Dict[str, Any]:
    br = get_breaker(path, breaker_id)
    if not br:
        return {'ok': False, 'error': 'unknown_breaker'}
    # Priorizar entity_id si es una entidad de HA válida
    entity_id = br.get('entity_id')
    if entity_id and '.' in str(entity_id):
        device_id = entity_id
        print(f"breaker_service.pulse: using HA entity_id={entity_id} duration_ms={duration_ms} (breaker id={breaker_id})")
    else:
        device_id = (br.get('device_id') or br.get('tuya_device') or br.get('tuya_id') or br.get('tuya') or entity_id or '')
        print(f"breaker_service.pulse: using device_id={device_id} (not HA entity) duration_ms={duration_ms} (breaker id={breaker_id})")
    tuya_res = await _run_tuya_pulse(device_id, duration_ms)
    print(f"breaker_service.pulse: tuya_res={tuya_res}")
    return {'ok': True, 'breaker': br, 'tuya': tuya_res}


async def sync_all_breakers_from_ha(path: str) -> Dict[str, Any]:
    """Obtiene todos los estados via /api/states y sincroniza breakers locales.

    Devuelve: {
      ok: bool,
      updated: [ { id, estado, power?, energy?, voltage?, current? } ],
      error?: str
    }
    """
    if not (HA_URL and HA_TOKEN):
        return {'ok': False, 'error': 'ha_not_configured'}
    import aiohttp
    headers = {'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{HA_URL}/api/states", headers=headers) as resp:
                if resp.status != 200:
                    return {'ok': False, 'error': f'status_{resp.status}'}
                states = await resp.json()
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    data = load_data(path)
    breakers: List[Dict[str, Any]] = data.get('breakers', [])
    updated_list: List[Dict[str, Any]] = []
    dirty = False

    # Índice rápido de estados por entity_id
    states_index: Dict[str, Dict[str, Any]] = {s.get('entity_id'): s for s in states if s.get('entity_id')}

    # Helper: coerción numérica (acepta "123.4V", "10A", etc.)
    num_re = re.compile(r'^\s*([0-9]+(?:[\.,][0-9]+)?)')
    def _coerce(val):
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            m = num_re.match(val)
            if not m:
                return None
            txt = m.group(1).replace(',', '.')
            try:
                return float(txt)
            except Exception:
                return None
        return None

    # Decodificador Tuya base64 (DP): dpid(1) type(1) len(2) value(len)
    def _decode_tuya_b64(b64_text: str) -> Dict[str, float]:
        out: Dict[str, float] = {}
        try:
            raw = base64.b64decode(b64_text)
        except Exception:
            return out
        i = 0
        while i + 4 <= len(raw):
            dpid = raw[i]
            dtype = raw[i+1]
            ln = int.from_bytes(raw[i+2:i+4], 'big')
            start = i + 4
            end = start + ln
            if end > len(raw):
                break
            val_bytes = raw[start:end]
            i = end
            val = None
            if dtype in (0x02, 0x04, 0x00):  # value entero
                try:
                    val = int.from_bytes(val_bytes, 'big')
                except Exception:
                    val = None
            if val is None:
                continue
            if dpid == 18:  # corriente mA
                out['current'] = round(val / 1000.0, 3)
            elif dpid == 19:  # potencia deci-W
                out['power'] = round(val / 10.0, 2)
            elif dpid == 20:  # voltaje deci-V
                out['voltage'] = round(val / 10.0, 2)
            elif dpid in (101, 102, 21):  # energía (heurística)
                # 21 a veces Wh acumulados; 101/102 centésimas kWh
                if val > 100000:  # grande -> Wh
                    out['energy'] = round(val / 1000.0, 3)
                else:
                    out['energy'] = round(val / 100.0, 3)
        return out

    # Auto-descubrimiento de sensores relacionados si no se definieron explicitamente
    # Regla: partir del nombre base del switch (switch.xxx) => buscar sensor.xxx_power / _voltage etc
    for b in breakers:
        base_entity = b.get('entity_id')
        if not base_entity or '.' not in base_entity:
            continue
        base_name = base_entity.split('.', 1)[1]
        # Si ya hay métricas cargadas, saltar (se actualizarán abajo igualmente)
        # Localizar sensores candidatos
        # Añadimos sufijos comunes y variantes trifásicas (phase_a) para cubrir sensores con nombres distintos
        for metric, suffixes in {
            'power': ['power', 'active_power', 'current_power', 'power_w', 'phase_a_power', 'phase_a_active_power'],
            'voltage': ['voltage', 'voltage_v', 'phase_a_voltage', 'phase_a_phase_voltage'],
            'current': ['current', 'current_a', 'phase_a_current', 'phase_a_i'],
            'energy': ['energy', 'energy_total', 'total_energy', 'energy_kwh', 'phase_a_energy']
        }.items():
            explicit_key = f'{metric}_entity'
            if b.get(explicit_key):
                continue  # ya configurado manualmente
            for suf in suffixes:
                cand = f'sensor.{base_name}_{suf}'
                if cand in states_index:
                    b[explicit_key] = cand
                    dirty = True
                    break

    # Ahora iterar nuevamente para actualizar estado y métricas
    for b in breakers:
        changed = False
        metrics_changed: Dict[str, Any] = {}
        primary_entity = b.get('entity_id')
        # Actualizar estado desde la entidad primaria si existe
        st_primary = states_index.get(primary_entity)
        if st_primary:
            new_state_val = st_primary.get('state')
            new_estado = True if new_state_val == 'on' else False
            if b.get('estado') != new_estado:
                b['estado'] = new_estado
                changed = True
        # Recolectar métricas de entidades específicas y fallback atributos del switch
        candidate_entities = [primary_entity]
        for key in ('power_entity', 'energy_entity', 'voltage_entity', 'current_entity'):
            ent = b.get(key)
            if ent and ent not in candidate_entities:
                candidate_entities.append(ent)
        collected: Dict[str, Any] = {}
        for ent in candidate_entities:
            st = states_index.get(ent)
            if not st:
                continue
            attrs = st.get('attributes') or {}
            # Prioridad: si la entidad es específica de la métrica, tomar su state directo
            for metric in ('power', 'energy', 'voltage', 'current'):
                spec_key = f'{metric}_entity'
                if b.get(spec_key) == ent:
                    val = _coerce(st.get('state')) or _coerce(attrs.get(metric))
                    if val is not None:
                        collected[metric] = val
            # Atributos genéricos
            mapping = {
                'power': ['power', 'current_power_w', 'power_w', 'instant_power', 'active_power'],
                'energy': ['energy', 'today_energy_kwh', 'energy_kwh', 'total_energy', 'total_energy_kwh'],
                'voltage': ['voltage', 'voltage_v', 'current_voltage'],
                'current': ['current', 'current_a', 'current_ma'],
            }
            for metric, keys in mapping.items():
                if metric in collected:
                    continue
                for k in keys:
                    if k in attrs:
                        val = _coerce(attrs.get(k))
                        if val is not None:
                            # Escalados comunes
                            if k.endswith('_ma') and metric == 'current':
                                val /= 1000.0
                            collected[metric] = val
                            break
            # Intentar extraer de cadenas con unidades
            for metric in ('power', 'energy', 'voltage', 'current'):
                if metric in collected:
                    continue
                for k, v in attrs.items():
                    if isinstance(v, str) and metric in k.lower():
                        num = _coerce(v)
                        if num is not None:
                            collected[metric] = num
                            break
            # Base64 fallback (buscar valor que parezca base64 largo)
            if not all(m in collected for m in ('power', 'voltage', 'current')):
                for k, v in attrs.items():
                    if not isinstance(v, str) or len(v) < 16:
                        continue
                    if re.fullmatch(r'[A-Za-z0-9+/=]+', v):
                        decoded = _decode_tuya_b64(v)
                        for mk, mv in decoded.items():
                            collected.setdefault(mk, mv)
                        if all(m in collected for m in ('power', 'voltage', 'current')):
                            break
        # Aplicar métricas recogidas
        for metric in ('power', 'energy', 'voltage', 'current'):
            if metric in collected and collected[metric] is not None:
                if b.get(metric) != collected[metric]:
                    b[metric] = collected[metric]
                    metrics_changed[metric] = collected[metric]
                    changed = True
        if changed:
            dirty = True
            updated_list.append({'id': b.get('id'), 'estado': b.get('estado'), **metrics_changed})

    if dirty:
        save_data(path, data)
    return {'ok': True, 'updated': updated_list}
