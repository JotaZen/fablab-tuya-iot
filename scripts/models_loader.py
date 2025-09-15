import json
from typing import Dict, Any, List, Optional


def load_data(path: str) -> Dict[str, Any]:
    """Carga y devuelve el contenido JSON desde path. Si falla, devuelve estructuras vacías."""
    try:
        with open(path, 'r', encoding='utf8') as f:
            return json.load(f)
    except Exception:
        return {"tarjetas": [], "breakers": [], "arduinos": []}


def save_data(path: str, data: Dict[str, Any]) -> None:
    """Guarda el diccionario en path como JSON."""
    with open(path, 'w', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_models(path: str) -> Dict[str, List[Dict[str, Any]]]:
    return load_data(path)


def get_breaker(path: str, breaker_id: str) -> Optional[Dict[str, Any]]:
    data = load_data(path)
    for b in data.get('breakers', []):
        if b.get('id') == breaker_id:
            return b
    return None


def set_breaker_state(path: str, breaker_id: str, state: bool) -> Optional[Dict[str, Any]]:
    """Setea estado del breaker y persiste en el JSON. Devuelve el breaker modificado o None."""
    data = load_data(path)
    modified = False
    for b in data.get('breakers', []):
        if b.get('id') == breaker_id:
            b['estado'] = bool(state)
            modified = True
            break
    if modified:
        save_data(path, data)
        return b
    return None


def toggle_breaker(path: str, breaker_id: str) -> Optional[Dict[str, Any]]:
    data = load_data(path)
    for b in data.get('breakers', []):
        if b.get('id') == breaker_id:
            b['estado'] = not bool(b.get('estado', False))
            save_data(path, data)
            return b
    return None


def get_tarjeta_for_breaker(path: str, breaker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Si el breaker referencia una tarjeta por id, devolverla."""
    tarjeta_id = breaker.get('tarjeta')
    if not tarjeta_id:
        return None
    data = load_data(path)
    for t in data.get('tarjetas', []):
        if t.get('id') == tarjeta_id:
            return t
    return None


def update_breaker_fields(path: str, breaker_id: str, **fields) -> Optional[Dict[str, Any]]:
    """Actualiza campos arbitrarios del breaker y persiste.

    Devuelve el breaker actualizado o None si no existe.
    """
    data = load_data(path)
    updated = None
    for b in data.get('breakers', []):
        if b.get('id') == breaker_id:
            for k, v in fields.items():
                b[k] = v
            updated = b
            break
    if updated is not None:
        save_data(path, data)
    return updated


def set_tarjeta_saldo(path: str, tarjeta_id: str, nuevo_saldo: float) -> Optional[Dict[str, Any]]:
    """Establece el saldo absoluto de una tarjeta y sincroniza breakers asociados.

    - Persiste en JSON.
    - Enciende/apaga breakers asociados según saldo > 0.
    Devuelve la tarjeta actualizada o None si no existe.
    """
    data = load_data(path)
    tarjetas = data.get('tarjetas', [])
    t = None
    for tt in tarjetas:
        if tt.get('id') == tarjeta_id:
            t = tt
            break
    if t is None:
        return None
    try:
        val = float(nuevo_saldo)
    except Exception:
        val = 0.0
    t['saldo'] = round(val, 6)
    # toggle breakers asociados
    desired_on = t['saldo'] > 0.0
    for b in data.get('breakers', []):
        if b.get('tarjeta') == tarjeta_id:
            if bool(b.get('estado')) != desired_on:
                # actualizar estado y persistir vía helper existente
                set_breaker_state(path, b.get('id'), desired_on)
    save_data(path, data)
    return t


def adjust_tarjeta_saldo(path: str, tarjeta_id: str, delta: float) -> Optional[Dict[str, Any]]:
    """Ajusta el saldo de una tarjeta sumando 'delta' (puede ser negativo).

    - Limita inferior a 0.0
    - Enciende/apaga breakers según saldo resultante
    - Persiste y devuelve la tarjeta actualizada
    """
    data = load_data(path)
    tarjetas = data.get('tarjetas', [])
    t = None
    for tt in tarjetas:
        if tt.get('id') == tarjeta_id:
            t = tt
            break
    if t is None:
        return None
    try:
        current = float(t.get('saldo') or 0.0)
    except Exception:
        current = 0.0
    try:
        d = float(delta)
    except Exception:
        d = 0.0
    new_val = max(0.0, current + d)
    t['saldo'] = round(new_val, 6)
    desired_on = new_val > 0.0
    for b in data.get('breakers', []):
        if b.get('tarjeta') == tarjeta_id:
            if bool(b.get('estado')) != desired_on:
                set_breaker_state(path, b.get('id'), desired_on)
    save_data(path, data)
    return t
