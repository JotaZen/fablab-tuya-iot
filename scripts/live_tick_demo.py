"""Demostración en consola: ejecuta ticks de 1s y muestra por pantalla lo que se descuenta.
Usage: python scripts/live_tick_demo.py --iterations 10
Este script modifica `scripts/data.json` (hace backup `.bak` antes).
"""
import sys, os, time, json, shutil, argparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.consumption_manager import ConsumptionManager
from scripts import models_loader


def normalize_power(report, v, i):
    """Misma heurística que en consumption_manager para obtener W."""
    inferred = None
    try:
        if v is not None and i is not None:
            inferred = float(v) * float(i)
    except Exception:
        inferred = None
    power = report
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


def print_state(prefix, data):
    print(prefix)
    for t in data.get('tarjetas', []):
        print(f"  Tarjeta {t.get('id')}: saldo={t.get('saldo')}")
    for b in data.get('breakers', []):
        print(f"  Breaker {b.get('id')[:8]}: estado={b.get('estado')}, power={b.get('power')}, voltage={b.get('voltage')}, current={b.get('current')}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--iterations', '-n', type=int, default=10, help='Número de ticks (1s) a ejecutar')
    args = parser.parse_args()

    DATA_P = os.path.join(os.path.dirname(__file__), 'data.json')
    BACKUP = DATA_P + '.bak'
    if not os.path.exists(BACKUP):
        shutil.copyfile(DATA_P, BACKUP)
        print('Backup creado en', BACKUP)
    else:
        print('Backup existente en', BACKUP)

    cm = ConsumptionManager(DATA_P)

    print('\nEstado inicial:')
    data0 = models_loader.load_data(DATA_P)
    print_state('ANTES', data0)

    for it in range(args.iterations):
        print('\n=== Tick', it + 1, '===')
        # mostrar lo que se calcula antes
        data_before = models_loader.load_data(DATA_P)
        for b in data_before.get('breakers', []):
            rep = b.get('power')
            v = b.get('voltage')
            i = b.get('current')
            norm = normalize_power(rep, v, i)
            energy_ws = None
            if norm is not None:
                energy_ws = round(float(norm) * 1.0, 6)
            tar = b.get('tarjeta')
            tar_obj = next((t for t in data_before.get('tarjetas', []) if t.get('id') == tar), None)
            saldo_before = tar_obj.get('saldo') if tar_obj else None
            print(f"Breaker {b.get('id')[:8]}: reported={rep}, inferred={v}*{i}={round(v*i,6) if v and i else None}, normalized_W={norm}, energy_ws(1s)={energy_ws}, tarjeta={tar}, saldo_before={saldo_before}")

        # ejecutar tick de 1 segundo
        cm._tick(1.0)

        # leer estado después y mostrar diferencias
        data_after = models_loader.load_data(DATA_P)
        for b in data_after.get('breakers', []):
            cid = b.get('id')
            cons = b.get('consumption_last_ws')
            pwr = b.get('consumption_power_w')
            estado = b.get('estado')
            tar = b.get('tarjeta')
            tar_obj = next((t for t in data_after.get('tarjetas', []) if t.get('id') == tar), None)
            saldo_after = tar_obj.get('saldo') if tar_obj else None
            print(f"After Breaker {cid[:8]}: consumption_last_ws={cons}, consumption_power_w={pwr}, estado={estado}, tarjeta_saldo_after={saldo_after}")

        # esperar 1 segundo real antes del próximo tick
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            print('\nInterrumpido por usuario')
            break

    print('\nDemo finalizada. Data.json modificado. Backup guardado en', BACKUP)
    print('Si quieres restaurar el backup ejecuta:')
    print(f"  cp {BACKUP} {DATA_P}  # o copia equivalente en Windows")