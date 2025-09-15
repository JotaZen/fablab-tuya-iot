import json
from typing import Any, Dict, Optional
from .config import DATA_PATH

# Almacenamiento simple basado en JSON

def load_data() -> Dict[str, Any]:
    try:
        with open(DATA_PATH, 'r', encoding='utf8') as f:
            return json.load(f)
    except Exception:
        return {"tarjetas": [], "breakers": [], "arduinos": []}


def save_data(data: Dict[str, Any]) -> None:
    tmp = DATA_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    import os
    os.replace(tmp, DATA_PATH)


def get_tarjeta(data: Dict[str, Any], tarjeta_id: str) -> Optional[Dict[str, Any]]:
    return next((t for t in data.get('tarjetas', []) if t.get('id') == tarjeta_id), None)


def get_breaker(data: Dict[str, Any], breaker_id: str) -> Optional[Dict[str, Any]]:
    return next((b for b in data.get('breakers', []) if b.get('id') == breaker_id), None)
