Mini interfaz web para Tuya + Arduino

Archivos:

- `web_ui.py`: servidor aiohttp + websocket + servidor TCP para recibir datos del Arduino.
- `static/index.html`: interfaz mínima que muestra los eventos y botones para encender/apagar (llama a `/tuya/encender` y `/tuya/apagar`).

Uso básico:

1. Instala dependencias (recomendado dentro de un virtualenv):

```bash
python -m pip install -r requirements.txt
```

2. Ejecuta el servidor:

```bash
python scripts/web_ui.py
```

3. En el Arduino, envía datos como POST JSON a `http://<HOST>:9111/arduino` con payload `{ "uid": "...", "seconds": 30, "breaker_id": "b1" }`, o abre una conexión TCP al puerto 9999 y envia líneas con JSON o `uid=...;seconds=...;breaker_id=b1`.

Formatos JSON y persistencia

- `scripts/breakers.json` (persistencia):

```json
{
  "breakers": [
    {
      "id": "b1",
      "ha_entity": "switch.tuya_b1",
      "estado": false,
      "tarjeta_uid": null,
      "saldo": 0.0,
      "consumption_per_sec": 1.0
    }
  ],
  "tarjetas": [{ "uid": "RFID1", "saldo": 30.0 }]
}
```

- Arduino -> servidor (POST `/arduino`) payload ejemplo:

```json
{ "uid": "RFID1", "seconds": 30, "breaker_id": "b1" }
```

Lógica de saldo y consumo

- Cuando el servidor recibe un POST con `uid` y `breaker_id`, suma `seconds` (u `amount`) al `tarjeta.saldo` y al `breaker.saldo`.
- El servidor ejecuta un loop de consumo cada segundo que, para cada `breaker` con `estado == true` y asociado a una `tarjeta_uid`, descuenta `consumption_per_sec` unidades del `tarjeta.saldo` y del `breaker.saldo`.
- Si la `tarjeta.saldo` llega a 0, el `breaker.estado` se pone a `false` (apagado) automáticamente.

Endpoints importantes

- POST `/arduino` — recibe lectura del Arduino. Requiere `X-API-KEY` si `API_KEY` está definido en el entorno.
- GET `/breakers` — devuelve lista de breakers con su estado.
- POST `/breakers/{id}/set` — body `{ "state":"on"|"off" }` para forzar estado.

UI

- La UI se conecta por WebSocket a `/ws` y recibe mensajes:
  - `{ "type":"breakers:list", "data": [...] }` al conectar
  - `{ "type":"breakers:update", "id":"b1", "state":"off", "saldo": 0.0 }` en cambios

4. Abre `http://<HOST>:8080/` en un navegador para ver eventos en tiempo real.

Integración con Tuya (opcional): exporta `TUYA_ENABLED=1` y configura `TUYA_TOKEN`, `TUYA_DEVICE_ID` y `TUYA_DEVICE_IP` en el entorno; el servidor intentará llamar a tinytuya cuando se pulse encender/apagar.
