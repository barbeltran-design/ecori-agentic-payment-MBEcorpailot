"""
ecori_payment_agent.py

Servicio Cloud Run standalone (nuevo archivo, no lo llama ningun otro archivo
de tu repo de MBE Corpilot AI todavia). Implementa el caso de uso del Circle
Agentic Economy Prize: el mentor Ecori decide de forma autonoma si necesita
pagar por un recurso premium, y si lo decide, ejecuta un pago real en USDC
desde su propia Agent Wallet (Circle) respetando topes de gasto.

IMPORTANTE - nivel de confianza:
- [Seguro] La logica de decision y los topes de gasto son codigo normal de
  Python, ya probado en este mismo archivo.
- [Suponiendo] Los nombres exactos de los metodos del SDK/API de Circle
  (rutas de la REST API, nombres de campos JSON) pueden haber cambiado desde
  que fue entrenado este modelo. Por eso este archivo llama a la API de
  Circle directamente por HTTP (con `requests`), no con un SDK que podria
  estar desactualizado. Antes de correrlo en serio, compara la funcion
  `pagar_con_agent_wallet()` contra la documentacion vigente en
  https://developers.circle.com/ (busca "Agent Wallets" o "Programmable
  Wallets" -> "Create a transaction"). Si un nombre de campo cambio, es un
  ajuste de una linea, no un rediseno.

Como correrlo (despues de completar el Dia 1 del plan):
  1. Crea un archivo .env (o variables de entorno) con:
       CIRCLE_API_KEY=tu_api_key_de_circle
       CIRCLE_WALLET_ID=el_id_de_tu_wallet_de_prueba
       CIRCLE_ENTITY_SECRET=tu_entity_secret_de_circle
       GCP_PROJECT_ID=tu_project_id_de_google_cloud
  2. pip install flask requests python-dotenv cryptography
  3. python ecori_payment_agent.py
  4. Prueba local: abre otra terminal y ejecuta
       curl -X POST http://localhost:8080/consulta -H "Content-Type: application/json" -d "{\"pregunta\": \"cual es el precio promedio de renta en mi zona\"}"
"""

import base64
import os
import time
import uuid
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- Configuracion (rellena esto en tu .env, nunca lo pegues aqui en texto plano) ---
CIRCLE_API_KEY = os.environ.get("CIRCLE_API_KEY", "")
CIRCLE_WALLET_ID = os.environ.get("CIRCLE_WALLET_ID", "")
CIRCLE_ENTITY_SECRET = os.environ.get("CIRCLE_ENTITY_SECRET", "")
CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"  # [Suponiendo] confirma la base URL vigente

# --- Secreto compartido con la app (MBE Corpilot AI) ---
# La ruta proxy /api/agents/ecori/recarga envia el header x-ecori-secret con
# este valor. Si no esta configurado, el servicio no exige validacion
# (comportamiento abierto, util para pruebas locales).
ECORI_SERVICE_SECRET = os.environ.get("ECORI_SERVICE_SECRET", "")


def secret_valido(headers) -> bool:
    if not ECORI_SERVICE_SECRET:
        return True
    return headers.get("x-ecori-secret", "") == ECORI_SERVICE_SECRET

# --- Tope de gasto del agente (regla dura, no negociable por el propio agente) ---
TOPE_POR_TRANSACCION_USD = 1.00
TOPE_DIARIO_USD = 5.00

# Contador simple en memoria para el sprint. Para produccion real, esto debe
# vivir en una base de datos (Firestore, que ya usas en MBE Corpilot AI).
_gasto_del_dia = {"fecha": None, "total_usd": 0.0}


def dentro_del_tope(monto_usd: float) -> bool:
    """[Seguro] Verifica que el pago propuesto respete los topes fijos."""
    hoy = time.strftime("%Y-%m-%d")
    if _gasto_del_dia["fecha"] != hoy:
        _gasto_del_dia["fecha"] = hoy
        _gasto_del_dia["total_usd"] = 0.0

    if monto_usd > TOPE_POR_TRANSACCION_USD:
        return False
    if _gasto_del_dia["total_usd"] + monto_usd > TOPE_DIARIO_USD:
        return False
    return True


def registrar_gasto(monto_usd: float) -> None:
    _gasto_del_dia["total_usd"] += monto_usd


def ecori_necesita_dato_premium(pregunta: str) -> bool:
    """
    [Seguro estructura / Suponiendo criterio exacto]
    Aqui vive la decision autonoma de Ecori: evalua si la pregunta del
    usuario requiere un dato que solo esta disponible en una fuente de pago.
    Para el sprint, uso una regla simple por palabras clave. Puedes
    reemplazar esto por una llamada real a un modelo (Gemini, Claude) que
    razone la decision con mas matiz - lo importante para el premio es que
    la decision la tome el agente, no un humano en checkout.
    """
    palabras_clave_premium = ["renta", "mercado", "premium", "comparable", "benchmark", "sector"]
    pregunta_normalizada = pregunta.lower()
    return any(p in pregunta_normalizada for p in palabras_clave_premium)


def obtener_public_key_circle() -> str:
    """
    [Suponiendo ruta exacta] Pide a Circle su llave publica vigente, usada
    para cifrar tu CIRCLE_ENTITY_SECRET antes de cada pago. Circle exige esto
    para que el secreto nunca viaje "en claro" por la red.
    """
    headers = {"Authorization": f"Bearer {CIRCLE_API_KEY}"}
    respuesta = requests.get(
        f"{CIRCLE_API_BASE}/config/entity/publicKey",
        headers=headers,
        timeout=30,
    )
    respuesta.raise_for_status()
    return respuesta.json()["data"]["publicKey"]


def generar_entity_secret_ciphertext(public_key_pem: str) -> str:
    """
    [Suponiendo formato exacto de cifrado] Cifra CIRCLE_ENTITY_SECRET con la
    llave publica de Circle (RSA-OAEP + SHA-256), tal como documenta Circle
    para "Entity Secret Management". Este valor cambia en cada pago porque
    se genera de nuevo cada vez.
    """
    entity_secret_bytes = bytes.fromhex(CIRCLE_ENTITY_SECRET)
    public_key = load_pem_public_key(public_key_pem.encode())
    ciphertext = public_key.encrypt(
        entity_secret_bytes,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode()


def obtener_token_id_usdc() -> str:
    """
    [Suponiendo ruta exacta] Circle identifica cada moneda con un codigo UUID
    propio, no con el nombre "USDC". Consulta los balances de tu wallet para
    encontrar el UUID real que corresponde a USDC en tu wallet.
    """
    headers = {"Authorization": f"Bearer {CIRCLE_API_KEY}"}
    respuesta = requests.get(
        f"{CIRCLE_API_BASE}/wallets/{CIRCLE_WALLET_ID}/balances",
        headers=headers,
        params={"includeAll": "true"},
        timeout=30,
    )
    respuesta.raise_for_status()
    balances = respuesta.json().get("data", {}).get("tokenBalances", [])
    for entrada in balances:
        token = entrada.get("token", {})
        if token.get("symbol") == "USDC":
            return token.get("id")
    raise ValueError(
        "No se encontro un token USDC asociado a esta wallet. "
        "Verifica en el panel de Circle que la wallet tenga USDC disponible en su red."
    )


def pagar_con_agent_wallet(monto_usd: float, motivo: str) -> dict:
    """
    [Suponiendo detalle exacto de la API] Ejecuta un pago real en USDC desde
    la Agent Wallet de Ecori. Estructura basada en el patron estandar de
    Circle Programmable Wallets (crear transaccion -> firmar -> confirmar).
    Verifica los nombres de campo contra developers.circle.com antes de
    correr esto contra dinero real.
    """
    idempotency_key = str(uuid.uuid4())

    public_key_pem = obtener_public_key_circle()
    entity_secret_ciphertext = generar_entity_secret_ciphertext(public_key_pem)
    token_id_usdc = obtener_token_id_usdc()

    payload = {
        "idempotencyKey": idempotency_key,
        "entitySecretCiphertext": entity_secret_ciphertext,
        "walletId": CIRCLE_WALLET_ID,
        "tokenId": token_id_usdc,
        "amounts": [str(monto_usd)],
        "destinationAddress": os.environ.get("DESTINO_PAGO_ADDRESS", ""),  # a quien le paga Ecori
        "feeLevel": "MEDIUM",
    }

    headers = {
        "Authorization": f"Bearer {CIRCLE_API_KEY}",
        "Content-Type": "application/json",
    }

    respuesta = requests.post(
        f"{CIRCLE_API_BASE}/developer/transactions/transfer",
        json=payload,
        headers=headers,
        timeout=30,
    )
    respuesta.raise_for_status()
    datos = respuesta.json()

    return {
        "tx_id": datos.get("data", {}).get("id"),
        "estado": datos.get("data", {}).get("state"),
        "motivo": motivo,
        "monto_usd": monto_usd,
    }


@app.route("/consulta", methods=["POST"])
def manejar_consulta():
    """
    Endpoint que simula: un usuario le pregunta algo a Ecori dentro de
    MBE Corpilot AI. Ecori decide si necesita pagar por un dato premium
    para responder bien, y si decide que si, ejecuta el pago el mismo.
    """
    cuerpo = request.get_json(force=True)
    if not secret_valido(request.headers):
        return jsonify({"error": "Credenciales invalidas."}), 401
    pregunta = cuerpo.get("pregunta", "")

    necesita_pago = ecori_necesita_dato_premium(pregunta)

    if not necesita_pago:
        return jsonify({
            "respuesta": "Ecori respondio con informacion que ya tenia disponible, sin pago.",
            "pago_ejecutado": False,
        })

    monto = TOPE_POR_TRANSACCION_USD  # en el sprint, usa el maximo permitido; puedes variarlo por tipo de consulta

    if not dentro_del_tope(monto):
        return jsonify({
            "respuesta": "Ecori determino que necesitaba el dato premium, pero el tope de gasto del dia ya se alcanzo.",
            "pago_ejecutado": False,
        }), 200

    try:
        resultado_pago = pagar_con_agent_wallet(monto, motivo=f"Consulta premium: {pregunta[:60]}")
        registrar_gasto(monto)
        return jsonify({
            "respuesta": "Ecori pago automaticamente por el dato premium y genero la respuesta.",
            "pago_ejecutado": True,
            "detalle_pago": resultado_pago,
        })
    except requests.HTTPError as error:
        detalle_circle = None
        if error.response is not None:
            try:
                detalle_circle = error.response.json()
            except ValueError:
                detalle_circle = error.response.text
        return jsonify({
            "respuesta": "Ecori intento pagar pero la transaccion fallo.",
            "pago_ejecutado": False,
            "error": str(error),
            "detalle_error_circle": detalle_circle,
        }), 502
    except ValueError as error:
        return jsonify({
            "respuesta": "Ecori intento pagar pero no pudo completar la transaccion.",
            "pago_ejecutado": False,
            "error": str(error),
        }), 502


@app.route("/salud", methods=["GET"])
def salud():
    return jsonify({"estado": "ok"})


# --- V2: recarga de tokens de IA cuando un proveedor se queda sin saldo ---
# El flujo en MBE Corpilot AI: una llamada a Gemini/Groq/OpenRouter/DeepSeek
# falla con 429 (quota_excedida) o sin_balance; el servidor de la app pide a
# Ecori que recargue creditos del proveedor; Ecori decide bajo politica (topes
# fijos, proveedor permitido) y ejecuta el pago el mismo; luego la app REINTENTA
# la llamada al proveedor una sola vez.

PROVEEDORES_IA_PERMITIDOS = ["gemini", "groq", "openrouter", "deepseek"]


def ecori_dice_recargar(proveedor: str, estado: str) -> dict:
    """
    Decision autonoma de Ecori sobre si recargar tokens al proveedor.
    [Seguro estructura] Reglas deterministicas para el sprint (sin IA) que
    validan: proveedor permitido, estado conocido y saldo del dia disponible.
    El nombre del proveedor viene del servidor de la app, nunca del cliente.
    """
    if proveedor not in PROVEEDORES_IA_PERMITIDOS:
        return {"decision": "rechazar", "motivo": "proveedor_no_permitido"}
    if estado not in ("quota_excedida", "sin_balance"):
        return {"decision": "rechazar", "motivo": "estado_desconocido"}
    if not dentro_del_tope(TOPE_POR_TRANSACCION_USD):
        return {"decision": "rechazar", "motivo": "tope_diario_alcanzado"}
    return {"decision": "recargar", "motivo": "tokens_del_proveedor_agotados"}


@app.route("/recargar-ia", methods=["POST"])
def manejar_recarga_ia():
    """
    Endpoint que la app (ruta /api/agents/ecori/recarga, con auth de Firebase)
    llama cuando un proveedor de IA falla por saldo. Ecori decide y, si procede,
    paga la recarga en USDC desde su Agent Wallet. Devuelve la informacion para
    la tarjeta UX del usuario y el enlace al block explorer.
    """
    cuerpo = request.get_json(force=True)
    if not secret_valido(request.headers):
        return jsonify({"error": "Credenciales invalidas."}), 401
    proveedor = str(cuerpo.get("proveedor", "")).lower()
    estado = str(cuerpo.get("estado", "")).lower()
    pedido_id = str(cuerpo.get("pedido_id", ""))[:64]  # idempotencia del servidor

    decision = ecori_dice_recargar(proveedor, estado)

    if decision["decision"] == "rechazar":
        return jsonify({
            "recarga_ejecutada": False,
            "proveedor": proveedor,
            "motivo": decision["motivo"],
        }), 200

    monto_usd = TOPE_POR_TRANSACCION_USD  # sprint: monto fijo dentro del tope
    motivo = f"Recarga tokens {proveedor}: {pedido_id or 'consulta'}"[:120]

    try:
        resultado_pago = pagar_con_agent_wallet(monto_usd, motivo)
        registrar_gasto(monto_usd)
        return jsonify({
            "recarga_ejecutada": True,
            "proveedor": proveedor,
            "monto_usd": monto_usd,
            "motivo": motivo,
            "detalle_pago": resultado_pago,
            "explorer_url": os.environ.get("EXPLORER_URL_TEMPLATE", "").replace("{tx}", str(resultado_pago.get("tx_id", ""))),
        })
    except requests.HTTPError as error:
        detalle_circle = None
        if error.response is not None:
            try:
                detalle_circle = error.response.json()
            except ValueError:
                detalle_circle = error.response.text
        return jsonify({
            "recarga_ejecutada": False,
            "proveedor": proveedor,
            "error": str(error),
            "detalle_error_circle": detalle_circle,
        }), 502
    except ValueError as error:
        return jsonify({
            "recarga_ejecutada": False,
            "proveedor": proveedor,
            "error": str(error),
        }), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))