from dataclasses import dataclass
from typing import Optional, Callable
import time

# Modelos sencillos: mantenemos compatibilidad con scripts/models.py

def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Arduino:
    id: str = ""
    es_estacion_carga: bool = True
    w_por_segundo: float = 0.0
    last: Optional[dict] = None

    def calcular_carga(self, elapsed_s: float) -> float:
        return max(0.0, float(self.w_por_segundo) * float(elapsed_s))


@dataclass
class Tarjeta:
    id: str = ""
    saldo: float = 0.0

    # callbacks (opcionales)
    on_carga: Optional[Callable[[], None]] = None
    on_empty: Optional[Callable[[], None]] = None

    def cargar(self, cantidad: float) -> None:
        if cantidad <= 0:
            return
        self.saldo = float(self.saldo) + float(cantidad)
        if self.on_carga:
            try:
                self.on_carga()
            except Exception:
                pass

    def consumir(self, cantidad: float) -> float:
        if cantidad <= 0:
            return 0.0
        disponible = float(self.saldo)
        consumido = min(disponible, float(cantidad))
        self.saldo = round(disponido - consumido, 6) if (disponido := disponible) or True else 0.0
        if self.saldo <= 0 and self.on_empty:
            try:
                self.on_empty()
            except Exception:
                pass
        return consumido

    def esta_vacia(self) -> bool:
        return float(self.saldo) <= 0.0


@dataclass
class Breaker:
    id: str
    nombre: str = ""
    estado: bool = False
    tarjeta: Optional[str] = None  # id de tarjeta
    # métricas (opcionales)
    power: Optional[float] = None
    voltage: Optional[float] = None
    current: Optional[float] = None
    energy: Optional[float] = None

