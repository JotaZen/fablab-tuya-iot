#!/usr/bin/env python3
"""Script de prueba para verificar que el control de switches funciona.

Este script simula lo que hace el botón ON/OFF en el HTML.
"""

import asyncio
import sys
import os

# Añadir el directorio scripts al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))

from breaker_service import toggle_breaker_service

DATA_PATH = 'scripts/data.json'

async def test_toggle():
    """Prueba toggle de un breaker."""
    print("=" * 60)
    print("TEST: Toggle de breaker 'agustin'")
    print("=" * 60)
    
    # Primera llamada: debería cambiar el estado
    print("\n1. Primera llamada (cambiar estado):")
    result1 = await toggle_breaker_service(DATA_PATH, 'agustin')
    print(f"   Result: {result1}")
    
    if result1.get('ok'):
        br = result1.get('breaker')
        print(f"   ✓ Estado nuevo: {'ON' if br.get('estado') else 'OFF'}")
        
        tuya = result1.get('tuya')
        if tuya:
            print(f"   ✓ Tuya response: success={tuya.get('success')} msg={tuya.get('msg')}")
            if 'emulated' in tuya.get('msg', ''):
                print("   ⚠️  ATENCIÓN: Respuesta emulada - no se envió comando real")
            elif 'HA service' in tuya.get('msg', ''):
                print("   ✓ Comando enviado a Home Assistant")
        
        ha = result1.get('ha')
        if ha:
            print(f"   ✓ HA response: {ha}")
    else:
        print(f"   ✗ Error: {result1.get('error')}")
    
    # Esperar un poco
    await asyncio.sleep(2)
    
    # Segunda llamada: debería volver al estado anterior
    print("\n2. Segunda llamada (volver al estado anterior):")
    result2 = await toggle_breaker_service(DATA_PATH, 'agustin')
    print(f"   Result: {result2}")
    
    if result2.get('ok'):
        br = result2.get('breaker')
        print(f"   ✓ Estado nuevo: {'ON' if br.get('estado') else 'OFF'}")
        
        tuya = result2.get('tuya')
        if tuya:
            print(f"   ✓ Tuya response: success={tuya.get('success')} msg={tuya.get('msg')}")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETADO")
    print("=" * 60)
    print("\n💡 NOTAS:")
    print("   - Si ves 'emulated: TUYA_ENABLED not set' significa que NO se envió comando real")
    print("   - Si ves 'called HA service' significa que SÍ se envió a Home Assistant")
    print("   - Verifica las variables de entorno HA_URL y HA_TOKEN si es necesario")

if __name__ == '__main__':
    asyncio.run(test_toggle())
