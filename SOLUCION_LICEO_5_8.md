## 🔧 SOLUCIÓN A PROBLEMAS DE LICEO-5 Y LICEO-8

### ✅ LICEO-5 - CORREGIDO

**Problema:**
- `entity_id` incorrecto: `switch.liceo_5` ❌
- Faltaba el sufijo `_interruptor`

**Solución aplicada:**
- Cambiado a: `switch.liceo_5_interruptor` ✅
- También actualizado en el array `entities`

**Próximo paso:**
Reiniciar el servidor para que use el nuevo entity_id:
```powershell
# Detener el servidor actual (Ctrl+C en la terminal)
# Reiniciar:
python scripts/web_ui.py
```

---

### ⚠️ LICEO-8 - REQUIERE VERIFICACIÓN EN HOME ASSISTANT

**Configuración actual:**
- ID: `eb7fb14604bce5beeczasf`
- Entity ID: `switch.liceo_8_interruptor`
- Tuya ID: `eb7fb14604bce5beeczasf`

**Posibles causas de "no aparecen datos en Home Assistant":**

1. **Dispositivo desconectado/offline**
   - Verifica en Home Assistant → Dispositivos → Busca "Liceo-8"
   - Si dice "unavailable", el breaker está apagado o sin conexión

2. **Entidad renombrada en Home Assistant**
   - Ve a Home Assistant → Configuración → Dispositivos y Servicios
   - Busca el dispositivo Tuya con ID `eb7fb14604bce5beeczasf`
   - Verifica que el entity_id sea exactamente `switch.liceo_8_interruptor`

3. **Integración Tuya desconectada**
   - Ve a Home Assistant → Configuración → Integraciones
   - Verifica que la integración Tuya esté activa
   - Recarga la integración si es necesario

**Cómo verificar manualmente en Home Assistant:**

1. Ve a: http://tu-home-assistant:8123/developer-tools/state
2. Busca: `switch.liceo_8_interruptor`
3. Si NO aparece → El entity_id es incorrecto o el dispositivo no está registrado
4. Si aparece con estado `unavailable` → El dispositivo está offline

**Soluciones posibles:**

Si el entity_id real es diferente:
1. Encuentra el entity_id correcto en Home Assistant
2. Actualiza `data.json` con el entity_id correcto
3. Reinicia web_ui.py

Si el dispositivo está offline:
1. Verifica que el breaker Liceo-8 tenga energía
2. Verifica la conexión WiFi del dispositivo
3. Reinicia el dispositivo físicamente
4. Espera a que se reconecte a Tuya Cloud

---

## 📋 VERIFICACIÓN RÁPIDA

Para verificar todas las entidades, ejecuta:
```powershell
python scripts/verificar_entidades_ha.py
```

Este script te mostrará:
- ✅ Entidades que existen y funcionan
- 🔴 Entidades no disponibles (offline)
- ❌ Entidades que no existen

---

## 🚀 PRÓXIMOS PASOS

1. **Reiniciar web_ui.py** para aplicar cambios de Liceo-5
2. **Ejecutar verificar_entidades_ha.py** para ver estado de Liceo-8
3. **Revisar Home Assistant** si Liceo-8 aparece como unavailable
4. **Probar manualmente** apagar/encender cada breaker desde la interfaz

