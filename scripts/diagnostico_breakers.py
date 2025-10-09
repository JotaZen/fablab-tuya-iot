#!/usr/bin/env python3
"""
Script de diagnóstico para identificar problemas de configuración en breakers.
Detecta duplicados de tuya_id, entity_id, y otras inconsistencias.
"""
import json
import sys
from pathlib import Path

# Ruta al data.json
BASE_DIR = Path(__file__).parent
DATA_PATH = BASE_DIR / 'data.json'

def load_data():
    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def diagnosticar():
    print("🔍 DIAGNÓSTICO DE BREAKERS")
    print("=" * 80)
    
    data = load_data()
    breakers = data.get('breakers', [])
    
    # Mapeos para detectar duplicados
    tuya_ids = {}
    entity_ids = {}
    nombres = {}
    
    problemas = []
    
    for idx, br in enumerate(breakers):
        br_id = br.get('id', f'UNKNOWN_{idx}')
        nombre = br.get('nombre', 'SIN_NOMBRE')
        tuya_id = br.get('tuya_id')
        entity_id = br.get('entity_id')
        estado = br.get('estado')
        
        print(f"\n📋 Breaker #{idx+1}: {nombre}")
        print(f"   ID: {br_id}")
        print(f"   Tuya ID: {tuya_id}")
        print(f"   Entity ID: {entity_id}")
        print(f"   Estado: {'ON ✅' if estado else 'OFF ❌'}")
        
        # Detectar duplicados de tuya_id
        if tuya_id:
            if tuya_id in tuya_ids:
                problema = f"🔴 DUPLICADO TUYA_ID: {nombre} y {tuya_ids[tuya_id]} comparten tuya_id={tuya_id}"
                print(f"   {problema}")
                problemas.append(problema)
            else:
                tuya_ids[tuya_id] = nombre
        else:
            problema = f"⚠️  ADVERTENCIA: {nombre} NO tiene tuya_id"
            print(f"   {problema}")
            problemas.append(problema)
        
        # Detectar duplicados de entity_id
        if entity_id:
            if entity_id in entity_ids:
                problema = f"🔴 DUPLICADO ENTITY_ID: {nombre} y {entity_ids[entity_id]} comparten entity_id={entity_id}"
                print(f"   {problema}")
                problemas.append(problema)
            else:
                entity_ids[entity_id] = nombre
        else:
            problema = f"⚠️  ADVERTENCIA: {nombre} NO tiene entity_id"
            print(f"   {problema}")
            problemas.append(problema)
        
        # Verificar que br_id sea único
        if br_id in nombres:
            problema = f"🔴 DUPLICADO ID: {nombre} y {nombres[br_id]} comparten id={br_id}"
            print(f"   {problema}")
            problemas.append(problema)
        else:
            nombres[br_id] = nombre
    
    # Resumen
    print("\n" + "=" * 80)
    print("📊 RESUMEN")
    print("=" * 80)
    print(f"Total de breakers: {len(breakers)}")
    print(f"Problemas encontrados: {len(problemas)}")
    
    if problemas:
        print("\n🔴 PROBLEMAS CRÍTICOS DETECTADOS:")
        for i, p in enumerate(problemas, 1):
            print(f"{i}. {p}")
        
        print("\n💡 SOLUCIÓN RECOMENDADA:")
        print("Los breakers con IDs duplicados controlarán el MISMO dispositivo físico.")
        print("Debes corregir los tuya_id y entity_id en data.json para que cada breaker")
        print("tenga identificadores únicos que correspondan a su dispositivo real.")
    else:
        print("\n✅ No se encontraron problemas de configuración!")
    
    print("\n" + "=" * 80)

if __name__ == '__main__':
    try:
        diagnosticar()
    except Exception as e:
        print(f"❌ Error ejecutando diagnóstico: {e}")
        sys.exit(1)
