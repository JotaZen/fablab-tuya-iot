import os
import asyncio
import json
from typing import Optional, Dict, Any
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

    # tuya (allow entity_id as fallback identifier per user request)
    device_id = br.get('device_id') or br.get('tuya_device') or br.get('entity_id')
    action = 'encender' if state else 'apagar'
    tuya_res = await _run_tuya_action(device_id or '', action)
    # normalize older 'ok' key if present
    if isinstance(tuya_res, dict) and 'ok' in tuya_res:
        tuya_res['success'] = bool(tuya_res.pop('ok'))
    tuya_res.setdefault('action', action)
    res['tuya'] = tuya_res

    # home assistant
    entity = br.get('entity_id')
    if entity and HA_URL and HA_TOKEN:
        svc = 'turn_on' if state else 'turn_off'
        ha_res = await _call_ha_service(entity, svc)
        res['ha'] = ha_res

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
    # call tuya/ha for the new state
    device_for_tuya = updated.get('device_id') or updated.get('tuya_device') or updated.get('entity_id') or ''
    print(f"breaker_service.toggle: using device_for_tuya={device_for_tuya}")
    tuya = await _run_tuya_action(device_for_tuya, 'encender' if new_state else 'apagar')
    if isinstance(tuya, dict) and 'ok' in tuya:
        tuya['success'] = bool(tuya.pop('ok'))
    tuya.setdefault('action', 'toggle')
    ha = None
    if updated.get('entity_id') and HA_URL and HA_TOKEN:
        svc = 'turn_on' if new_state else 'turn_off'
        ha = await _call_ha_service(updated.get('entity_id'), svc)
    # debug
    print(f"toggle_breaker_service: breaker={breaker_id} {current} -> {new_state} tuya_success={tuya.get('success')} msg={tuya.get('msg')}")
    return {'ok': True, 'breaker': updated, 'tuya': tuya, 'ha': ha}


async def pulse_breaker_service(path: str, breaker_id: str, duration_ms: int = 500) -> Dict[str, Any]:
    br = get_breaker(path, breaker_id)
    if not br:
        return {'ok': False, 'error': 'unknown_breaker'}
    device_id = br.get('device_id') or br.get('tuya_device') or br.get('entity_id') or ''
    print(f"breaker_service.pulse: device_id={device_id} duration_ms={duration_ms}")
    tuya_res = await _run_tuya_pulse(device_id, duration_ms)
    print(f"breaker_service.pulse: tuya_res={tuya_res}")
    return {'ok': True, 'breaker': br, 'tuya': tuya_res}
