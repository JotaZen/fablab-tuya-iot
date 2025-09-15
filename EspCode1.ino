#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecure.h>
#include <SPI.h>
#include <MFRC522.h>

// ---------------- CONFIG WIFI ----------------
const char* ssid = "Olimpiadas";
const char* password = "Es secreto";

// ---------------- GOOGLE SHEETS ----------------
const char* sheetUrl = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTGMNdwV8TfLTfnMhWM7JQfokb67YaS11Yw0A0Rb5T7RvDmbU1DMFDrjdpybGuX0qA3f3Ymnmqbs44M/pub?gid=0&single=true&output=csv";
String ipObtenida = "";  // se obtiene desde Google Sheets

// ---------------- RFID ----------------
#define SS_PIN D4   // Pin SDA del MFRC522
#define RST_PIN D3  // Pin RST del MFRC522
MFRC522 mfrc522(SS_PIN, RST_PIN);

const char* espID = "ESP8266_03";   // Identificador del ESP

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(115200);
  delay(1000);

  WiFi.begin(ssid, password);
  Serial.print("Conectando a WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi conectado!");
  Serial.print("IP local: ");
  Serial.println(WiFi.localIP());

  obtenerIPdesdeGoogleSheet();
  Serial.print("IP obtenida desde Google Sheet: ");
  Serial.println(ipObtenida);

  SPI.begin();
  mfrc522.PCD_Init();
  Serial.println("Lector RFID listo. Acerca una tarjeta...");
}

// ---------------- LOOP ----------------
void loop() {
  // Verificar si hay una tarjeta presente
  if (!mfrc522.PICC_IsNewCardPresent()) return;
  if (!mfrc522.PICC_ReadCardSerial()) return;

  // Construir UID en formato string
  String datoNFC = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    datoNFC += String(mfrc522.uid.uidByte[i], HEX);
  }
  datoNFC.toUpperCase();

  // 👇 Imprimir en el monitor serie
  Serial.println("==============================");
  Serial.print("Tarjeta detectada. UID: ");
  Serial.println(datoNFC);
  Serial.println("==============================");

  if (ipObtenida.length() > 0) {
    enviarJsonAPrivada(ipObtenida, datoNFC);
  }

  // Detener lectura hasta la siguiente tarjeta
  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
}

// =======================================================
// ---------------- FUNCIONES ----------------

void obtenerIPdesdeGoogleSheet() {
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClientSecure client;
    client.setInsecure();
    HTTPClient http;
    http.begin(client, sheetUrl);
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);

    int httpCode = http.GET();
    if (httpCode == HTTP_CODE_OK) {
      String payload = http.getString();

      int primerSalto = payload.indexOf('\n');
      String primeraLinea = (primerSalto > 0) ? payload.substring(0, primerSalto) : payload;

      int primerComa = primeraLinea.indexOf(',');
      if (primerComa > 0) {
        ipObtenida = primeraLinea.substring(0, primerComa);
      } else {
        ipObtenida = primeraLinea;
      }
      ipObtenida.trim();
    } else {
      Serial.printf("Error en HTTP GET: %d\n", httpCode);
    }
    http.end();
  }
}

void enviarJsonAPrivada(String ip, String datoNFC) {
  if (WiFi.status() == WL_CONNECTED && ip.length() > 0) {
    WiFiClient client;
    HTTPClient http;

    String url = "http://" + ip + "/rfid";
    http.begin(client, url);
    http.addHeader("Content-Type", "application/json");

    String jsonPayload = "{\"id\":\"" + String(espID) + "\",\"nfc\":\"" + datoNFC + "\"}";

    Serial.print("Enviando JSON: ");
    Serial.println(jsonPayload);

    int httpResponseCode = http.POST(jsonPayload);

    if (httpResponseCode > 0) {
      Serial.printf("Respuesta HTTP: %d\n", httpResponseCode);
      String response = http.getString();
      Serial.println("Respuesta del servidor:");
      Serial.println(response);
    } else {
      Serial.printf("Error en POST: %s\n", http.errorToString(httpResponseCode).c_str());
    }
    http.end();
  }
}
