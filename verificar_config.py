#!/usr/bin/env python3
"""Script de verificación: comprueba que config.py se importa correctamente."""

import sys
import os

print("=" * 60)
print("TEST: Verificación de configuración")
print("=" * 60)

# Test 1: Importar config.py
print("\n1. Importando config.py...")
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
    import config
    print("   ✅ config.py importado correctamente")
except Exception as e:
    print(f"   ❌ ERROR al importar config.py: {e}")
    sys.exit(1)

# Test 2: Verificar HA_URL
print("\n2. Verificando HA_URL...")
if hasattr(config, 'HA_URL'):
    ha_url = config.HA_URL
    if ha_url and ha_url != "http://localhost:8123":
        print(f"   ✅ HA_URL configurado: {ha_url[:30]}...")
    elif ha_url == "http://localhost:8123":
        print(f"   ⚠️  HA_URL usa valor por defecto: {ha_url}")
    else:
        print(f"   ❌ HA_URL está vacío")
else:
    print("   ❌ HA_URL no definido en config.py")

# Test 3: Verificar HA_TOKEN
print("\n3. Verificando HA_TOKEN...")
if hasattr(config, 'HA_TOKEN'):
    ha_token = config.HA_TOKEN
    if ha_token and len(ha_token) > 10:
        print(f"   ✅ HA_TOKEN configurado (longitud: {len(ha_token)})")
        print(f"   Token (primeros 20 chars): {ha_token[:20]}...")
    else:
        print(f"   ❌ HA_TOKEN está vacío o muy corto")
else:
    print("   ❌ HA_TOKEN no definido en config.py")

# Test 4: Verificar tuya_client puede importar config
print("\n4. Verificando tuya_client.py puede usar config...")
try:
    from scripts import tuya_client
    # Verificar que las variables están disponibles
    if hasattr(tuya_client, 'HA_URL') and hasattr(tuya_client, 'HA_TOKEN'):
        print(f"   ✅ tuya_client tiene HA_URL y HA_TOKEN")
        print(f"   HA_URL en tuya_client: {tuya_client.HA_URL if tuya_client.HA_URL else 'None'}")
        print(f"   HA_TOKEN en tuya_client: {'set' if tuya_client.HA_TOKEN else 'NOT set'}")
    else:
        print(f"   ⚠️  tuya_client no tiene HA_URL/HA_TOKEN definidos")
except Exception as e:
    print(f"   ❌ ERROR: {e}")

# Test 5: Simular llamada a perform_action
print("\n5. Simulando llamada a perform_action...")
try:
    from scripts.tuya_client import perform_action
    
    # Llamada de prueba con entity_id válido
    success, msg = perform_action('switch.test', 'on')
    print(f"   Resultado: success={success} msg={msg}")
    
    if 'emulated' in msg.lower():
        print("   ❌ PROBLEMA: La llamada fue emulada (HA_URL/HA_TOKEN no funcionan)")
    elif 'HA service' in msg or 'HA call error' in msg:
        print("   ✅ CORRECTO: Se intentó llamar a Home Assistant")
    else:
        print(f"   ⚠️  Resultado inesperado: {msg}")
        
except Exception as e:
    print(f"   ❌ ERROR: {e}")

print("\n" + "=" * 60)
print("RESUMEN")
print("=" * 60)
print("\n✅ = Todo correcto")
print("⚠️  = Atención requerida")
print("❌ = Error que debe corregirse")
print("\nSi ves '✅' en los pasos 1-4, el servidor debería funcionar correctamente.")
print("Si ves 'emulated' en el paso 5, verifica que HA_URL y HA_TOKEN estén bien configurados.")
