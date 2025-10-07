#!/bin/bash
# Script de instalación rápida para fablab-tuya-iot
# Instala todas las dependencias necesarias para ejecutar el sistema

set -e  # Detener en caso de error

echo "========================================="
echo "  Instalación fablab-tuya-iot"
echo "========================================="
echo ""

# Verificar si Python está instalado
if ! command -v python3 &> /dev/null; then
    echo "❌ ERROR: Python3 no está instalado"
    echo "Por favor instala Python 3.7 o superior"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python detectado: $PYTHON_VERSION"
echo ""

# Verificar si pip está instalado
if ! command -v pip3 &> /dev/null; then
    echo "❌ ERROR: pip3 no está instalado"
    echo "Instalando pip..."
    python3 -m ensurepip --upgrade
fi

echo "✓ pip3 está disponible"
echo ""

# Crear entorno virtual (opcional pero recomendado)
if [ ! -d "venv" ]; then
    echo "📦 Creando entorno virtual..."
    python3 -m venv venv
    echo "✓ Entorno virtual creado"
else
    echo "✓ Entorno virtual ya existe"
fi
echo ""

# Activar entorno virtual
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

# Actualizar pip
echo "⬆️  Actualizando pip..."
pip install --upgrade pip
echo ""

# Instalar dependencias desde requirements.txt
if [ -f "requirements.txt" ]; then
    echo "📚 Instalando dependencias desde requirements.txt..."
    pip install -r requirements.txt
    echo "✓ Dependencias instaladas"
else
    echo "⚠️  Advertencia: requirements.txt no encontrado"
    echo "Instalando dependencias mínimas..."
    pip install aiohttp websockets
fi
echo ""

# Verificar instalación de paquetes críticos
echo "🔍 Verificando instalación..."
MISSING_PACKAGES=()

python3 -c "import aiohttp" 2>/dev/null || MISSING_PACKAGES+=("aiohttp")
python3 -c "import websockets" 2>/dev/null || MISSING_PACKAGES+=("websockets")

if [ ${#MISSING_PACKAGES[@]} -eq 0 ]; then
    echo "✓ Todas las dependencias críticas están instaladas"
else
    echo "❌ Faltan paquetes: ${MISSING_PACKAGES[*]}"
    echo "Instalando paquetes faltantes..."
    pip install "${MISSING_PACKAGES[@]}"
fi
echo ""

# Crear archivo data.json si no existe
if [ ! -f "scripts/data.json" ]; then
    echo "📝 Creando scripts/data.json inicial..."
    mkdir -p scripts
    cat > scripts/data.json << 'EOF'
{
  "tarjetas": [],
  "breakers": [],
  "arduinos": []
}
EOF
    echo "✓ scripts/data.json creado"
else
    echo "✓ scripts/data.json ya existe"
fi
echo ""

# Crear archivo usage_limits.json si no existe
if [ ! -f "scripts/usage_limits.json" ]; then
    echo "📝 Creando scripts/usage_limits.json inicial..."
    mkdir -p scripts
    cat > scripts/usage_limits.json << 'EOF'
{
  "limites": {
    "tiempo_profe_segundos": 1800,
    "tiempo_ia_segundos": 900,
    "max_usos_profe": 5,
    "max_usos_ia": 3
  }
}
EOF
    echo "✓ scripts/usage_limits.json creado"
    echo ""
    echo "⚙️  Límites de uso configurados:"
    echo "   - Tiempo Profesor: 30 minutos (1800s)"
    echo "   - Tiempo IA: 15 minutos (900s)"
    echo "   - Usos máximos Profesor: 5"
    echo "   - Usos máximos IA: 3"
else
    echo "✓ scripts/usage_limits.json ya existe"
fi
echo ""

# Crear archivo .env de ejemplo si no existe
if [ ! -f ".env" ]; then
    echo "📝 Creando archivo .env de ejemplo..."
    cat > .env << 'EOF'
# Configuración Home Assistant
HA_URL=http://localhost:8123
HA_TOKEN=tu_token_aqui
HA_WS=ws://localhost:8123/api/websocket

# Configuración del servidor
UI_HOST=0.0.0.0
UI_PORT=9111

# API Key (opcional)
API_KEY=

# Habilitar consumo del lado servidor (0=deshabilitado, 1=habilitado)
# Recomendado: 0 si usas el tick desde el frontend
ENABLE_SERVER_CONSUMPTION=0
EOF
    echo "✓ Archivo .env creado"
    echo ""
    echo "⚠️  IMPORTANTE: Edita el archivo .env y configura tu HA_URL y HA_TOKEN"
else
    echo "✓ Archivo .env ya existe"
fi
echo ""

echo "========================================="
echo "  ✅ Instalación completada"
echo "========================================="
echo ""
echo "Próximos pasos:"
echo "1. Edita el archivo .env con tu configuración de Home Assistant"
echo "2. Ejecuta: ./run.sh para iniciar el servidor"
echo ""
