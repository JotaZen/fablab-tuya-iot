#!/usr/bin/env python3
"""
Script para verificar si las entidades de Home Assistant existen y están disponibles.
"""
import asyncio
import aiohttp
import json
from pathlib import Path

# Importar configuración
try:
    from config import HA_URL, HA_TOKEN
except:
    import os
    HA_URL = os.getenv('HA_URL', 'http://localhost:8123')
    HA_TOKEN = os.getenv('HA_TOKEN', '')

BASE_DIR = Path(__file__).parent
DATA_PATH = BASE_DIR / 'data.json'

async def verificar_entidades():
    """Verifica si las entidades de los breakers existen en Home Assistant."""
    
    if not HA_URL or not HA_TOKEN:
        print("❌ HA_URL o HA_TOKEN no configurados")
        return
    
    # Cargar data.json
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    breakers = data.get('breakers', [])
    
    print("🔍 VERIFICANDO ENTIDADES EN HOME ASSISTANT")
    print("=" * 80)
    print(f"Home Assistant: {HA_URL}")
    print("=" * 80)
    
    headers = {
        'Authorization': f'Bearer {HA_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    async with aiohttp.ClientSession() as session:
        for br in breakers:
            nombre = br.get('nombre', 'SIN_NOMBRE')
            entity_id = br.get('entity_id')
            
            print(f"\n📋 {nombre}")
            print(f"   Entity ID: {entity_id}")
            
            if not entity_id:
                print(f"   ⚠️  NO tiene entity_id configurado")
                continue
            
            # Consultar estado de la entidad
            url = f"{HA_URL}/api/states/{entity_id}"
            
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        state_data = await resp.json()
                        state = state_data.get('state', 'unknown')
                        last_changed = state_data.get('last_changed', 'N/A')
                        attributes = state_data.get('attributes', {})
                        
                        if state == 'unavailable':
                            print(f"   🔴 ENTIDAD NO DISPONIBLE (offline/desconectada)")
                        elif state == 'unknown':
                            print(f"   ⚠️  Estado desconocido")
                        else:
                            print(f"   ✅ Entidad existe y funciona")
                            print(f"      Estado actual: {state.upper()}")
                            print(f"      Última actualización: {last_changed}")
                            
                            # Mostrar algunos atributos útiles
                            if 'friendly_name' in attributes:
                                print(f"      Nombre: {attributes['friendly_name']}")
                            if 'device_class' in attributes:
                                print(f"      Tipo: {attributes['device_class']}")
                    
                    elif resp.status == 404:
                        print(f"   ❌ ENTIDAD NO EXISTE en Home Assistant")
                        print(f"      Verifica que el dispositivo esté configurado correctamente")
                    
                    else:
                        print(f"   ⚠️  Error HTTP {resp.status}")
                        
            except Exception as e:
                print(f"   ❌ Error consultando: {e}")
            
            # También verificar las entidades de sensores
            entities_list = br.get('entities', [])
            if entities_list:
                print(f"   📊 Verificando {len(entities_list)} entidades adicionales...")
                unavailable_count = 0
                missing_count = 0
                
                for ent in entities_list:
                    if not ent or ent == entity_id:
                        continue
                    
                    url = f"{HA_URL}/api/states/{ent}"
                    try:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                state_data = await resp.json()
                                if state_data.get('state') == 'unavailable':
                                    unavailable_count += 1
                            elif resp.status == 404:
                                missing_count += 1
                    except:
                        pass
                
                if unavailable_count > 0:
                    print(f"      ⚠️  {unavailable_count} sensores no disponibles")
                if missing_count > 0:
                    print(f"      ❌ {missing_count} sensores no existen")
                if unavailable_count == 0 and missing_count == 0:
                    print(f"      ✅ Todos los sensores OK")
    
    print("\n" + "=" * 80)
    print("✅ Verificación completada")
    print("=" * 80)

if __name__ == '__main__':
    asyncio.run(verificar_entidades())
