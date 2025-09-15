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
