Home assistant para dispositivos tuya iot + helpers python para olimpiadas 2025 INACAP


Para levantar home assistant es 

docker compose up -d

Crear cuenta, generar token desde 
->nombreusuario
->seguridad
->crear token
->copiar config_example.py a un nuevo archivo config.py en carpetas scripts/
->pegar en config.py el HA_TOKEN = os.environ.get("HA_TOKEN", "{aqui va el token de home assistant}")


Para instalar dependencias
pip install -r requirements.txt

Para correr el servidor
python scripts/web_ui.py

Interfaz web en http://localhost:9111/display
Panel admin en http://localhost:9111/

Conectarse wifi de los arduinos/breakers
cambiar ip en
https://docs.google.com/spreadsheets/d/1qQGLUHViWciwMprfzU2nE6iwLzcg1BBSGT_W7pE8SPM/edit?gid=0#gid=0 
para que coincida con la actual del servidor (comunicación con arduinos)
