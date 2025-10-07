#!/bin/bash
# Script para ejecutar el servidor Web UI de fablab-tuya-iot

set -e  # Detener en caso de error

echo "========================================="
echo "  Iniciando fablab-tuya-iot Web UI"
echo "========================================="
echo ""

# Cargar variables de entorno desde .env si existe
if [ -f ".env" ]; then
    echo "📋 Cargando configuración desde .env..."
    export $(cat .env | grep -v '^#' | grep -v '^\s*$' | xargs)
    echo "✓ Variables de entorno cargadas"
else
    echo "⚠️  Advertencia: .env no encontrado, usando valores por defecto"
    export UI_HOST="${UI_HOST:-0.0.0.0}"
    export UI_PORT="${UI_PORT:-9111}"
fi
echo ""

# Activar entorno virtual si existe
if [ -d "venv" ]; then
    echo "🔧 Activando entorno virtual..."
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
        # Windows (Git Bash)
        source venv/Scripts/activate
    else
        # Linux/Mac
        source venv/bin/activate
    fi
    echo "✓ Entorno virtual activado"
    echo ""
fi

# Verificar que el archivo web_ui.py existe
if [ ! -f "scripts/web_ui.py" ]; then
    echo "❌ ERROR: scripts/web_ui.py no encontrado"
    exit 1
fi

# Verificar que data.json existe
if [ ! -f "scripts/data.json" ]; then
    echo "⚠️  scripts/data.json no existe, creándolo..."
    mkdir -p scripts
    cat > scripts/data.json << 'EOF'
{
  "tarjetas": [],
  "breakers": [],
  "arduinos": []
}
EOF
    echo "✓ scripts/data.json creado"
    echo ""
fi

# Mostrar configuración
echo "📡 Configuración del servidor:"
echo "   - Host: ${UI_HOST:-0.0.0.0}"
echo "   - Puerto: ${UI_PORT:-9111}"
if [ -n "$HA_URL" ]; then
    echo "   - Home Assistant: $HA_URL"
else
    echo "   - Home Assistant: No configurado"
fi
echo ""

# Iniciar servidor
echo "🚀 Iniciando servidor..."
echo "========================================="
echo ""
echo "   Accede a la interfaz web en:"
echo "   http://localhost:${UI_PORT:-9111}"
echo ""
echo "   Presiona Ctrl+C para detener el servidor"
echo ""
echo "========================================="
echo ""

# Ejecutar el servidor
cd scripts
python3 web_ui.py
