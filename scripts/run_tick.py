"""Script de conveniencia para ejecutar un tick de consumo (1 s) y mostrar antes/después.
Usar desde la raíz del repo con `python scripts/run_tick.py`.
"""
import shutil, json, os, sys

# asegurar que la raíz del repo está en sys.path para importar el paquete scripts
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.consumption_manager import ConsumptionManager

P = os.path.join(os.path.dirname(__file__), 'data.json')
BACKUP = P + '.bak'
shutil.copyfile(P, BACKUP)
print('Backup creado:', BACKUP)
with open(P, 'r', encoding='utf8') as f:
    before = json.load(f)
print('Antes tarjetas:', before.get('tarjetas'))
print('Antes breakers (saldo keys if any):', [{k: v for k, v in b.items() if k in ('id','saldo','max_saldo','estado','power','voltage','current')} for b in before.get('breakers', [])])

cm = ConsumptionManager(P)
cm._tick(1.0)

with open(P, 'r', encoding='utf8') as f:
    after = json.load(f)
print('\nDespués tarjetas:', after.get('tarjetas'))
print('Después breakers (relevantes):', [{k: v for k, v in b.items() if k in ('id','estado','consumption_last_ws','consumption_power_w')} for b in after.get('breakers', [])])

# restaurar backup para que no queden cambios permanentes a menos que el usuario lo quiera
shutil.copyfile(BACKUP, P)
os.remove(BACKUP)
print('\nRestaurado backup. Si quieres que el cambio persista, elimina la restauración en este script.')