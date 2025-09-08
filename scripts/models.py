from dataclasses import dataclass, asdict, field
from typing import List, Optional, Callable
import time


@dataclass
class Arduino:
    id: str = ""
    w_por_segundo = 0
    es_estacion_carga: bool = True

    def calcular_carga(self, tiempo_ms: int) -> float:
        return self.w_por_segundo * (tiempo_ms / 1000)

def hora_actual_ms() -> int:
    return int(time.time() * 1000)

@dataclass
class Tarjeta:
    id: str = ""
    saldo = 0.0

    cargando_tarjeta_en: Optional[Arduino] = None
    cargando_tarjeta_desde: Optional[int] = None
    carga_acumulada: float = 0.0
    # callbacks
    on_carga: Optional = None
    on_empty: Optional = None
    
    def comenzar_carga(self, punto_carga: Arduino) -> None:
        if not punto_carga.es_estacion_carga:
            return 

        # si ya se esta cargando la tarjeta
        if self.cargando_tarjeta_en:
            # y no es una estacion de carga
            if not punto_carga.es_estacion_carga:
                self.finalizar_carga()
            else:
                self.acumular_carga()
                self.cargando_tarjeta_en = punto_carga
                self.cargando_tarjeta_desde = hora_actual_ms()
        
        else:
            # registra estacion de carga
            self.cargando_tarjeta_en = punto_carga
            # pc registra timepo inicio
            self.cargando_tarjeta_desde = hora_actual_ms()

    # cuanto lleva cargandose
    def tiempo_de_carga_total(self) -> int:
        if not self.cargando_tarjeta_desde:
            return 0
        hora_actual = hora_actual_ms()
        return hora_actual - self.cargando_tarjeta_desde

    def acumular_carga(self) -> None:
        if self.cargando_tarjeta_en:
            self.carga_acumulada += self.cargando_tarjeta_en.calcular_carga(self.tiempo_de_carga_total())


    def retirar_tarjeta(self) -> None:
        self.carga_acumulada = self.cargando_tarjeta_en.calcular_carga(self.tiempo_de_carga_total())
        self.cargando_tarjeta_en = None
        self.cargando_tarjeta_desde = None

    # cierra ciclo agregando la carga que se acumulo
    def registrar_carga(self, punto_carga: Arduino) -> None:
        if punto_carga.es_estacion_carga:
            return
        self.cargar(self.carga_acumulada)
        carga_acumulada = 0


    # abona carga a la tarjeta y gatilla eventos
    def cargar(self, cantidad: float) -> None:
        if cantidad <= 0:
            return
        self.saldo += float(cantidad)
        
        if self.on_carga:
            self.on_carga()

    def consumir(self, cantidad: float) -> float:
        if cantidad <= 0:
            return 0.0
        disponible = float(self.saldo)
        consumido = min(disponible, float(cantidad))
        self.saldo = round(disponible - consumido, 6)
        return consumido

    def esta_vacia(self) -> bool:
        return self.saldo <= 0.0






@dataclass
class Breaker:
    id: str = ""
    tarjeta: Optional[Tarjeta] = None	
    estado: bool = True

    on_apagar: Optional[Callable[[], None]] = None
    on_encender: Optional[Callable[[], None]] = None

    def __init__(self, id, tarjeta, estado, on_apagar, on_encender) -> None:
        self.id = id
        self.tarjeta = tarjeta
        self.estado = estado
        self.on_apagar = on_apagar
        self.on_encender = on_encender
        # encender cuando la tarjeta se carga
        if tarjeta:
            tarjeta.on_carga = self.encender
            tarjeta.on_empty = self.apagar
            
    def apagar(self) -> None:
        self.estado = False
        if self.on_apagar:
            self.on_apagar()

    def encender(self) -> None:
        self.estado = True
        if self.on_encender:
            self.on_encender()
