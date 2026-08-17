# Ecori Agentic Payment — MBE Corp-Ai-Lot

Submission for the **Build with Gemini XPRIZE — Circle Agentic Economy Prize**.

## What this is

`ecori_payment_agent.py` is a standalone microservice that gives **Ecori**, one of the AI mentors inside [MBE Corp-Ai-Lot](https://mbe-ai-copilot.vercel.app), the ability to autonomously decide when it needs a paid resource and execute a real USDC micropayment — with no human in the loop at checkout.

## How it works

1. A user asks Ecori a question.
2. Ecori evaluates whether answering well requires a premium data resource (currently a keyword-based decision; designed to be swapped for a Gemini/Claude-based reasoning call for production).
3. If yes, Ecori pays for that resource itself, in real USDC, from its own **Circle Agent Wallet**, respecting hard spending caps ($1 USD per transaction, $5 USD per day).
4. The payment is verified on-chain via Circle's Programmable Wallets API and a public block explorer.

## Proof of a real, autonomous payment

- **Transaction hash:** `0xdfac5cd1c7d09ed0b23dbfdb3024c49d11d6ba16ea7fb97ae65598f1c7f138d3`
- **Network:** Polygon Amoy (testnet)
- **Block explorer:** https://amoy.polygonscan.com/tx/0xdfac5cd1c7d09ed0b23dbfdb3024c49d11d6ba16ea7fb97ae65598f1c7f138d3
- **Status:** Success

## Transparency note

For this sprint, the payment recipient ("Normau Data Wallet") is a second wallet also owned by our team, representing a compiled-data resource from the MBE community — not an independent third-party vendor. This keeps the demo self-contained within a short sprint window. The architecture is vendor-agnostic: swapping in any real paid API (market data, pricing feeds, etc.) only requires changing the destination wallet address and the decision logic in `ecori_necesita_dato_premium()`.

## Architecture

- **Language:** Python 3.11+
- **Framework:** Flask
- **Payments:** Circle Programmable Wallets / Agent Wallets REST API (direct HTTP calls, no SDK, to avoid relying on a possibly-outdated SDK surface)
- **Chain:** Polygon Amoy testnet
- **Entity secret handling:** the raw entity secret is never sent to Circle; a fresh RSA-OAEP encrypted ciphertext is generated per request using Circle's published public key

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your own Circle credentials, never commit this file
python ecori_payment_agent.py
```

Then, in another terminal:

```bash
curl -X POST http://localhost:8080/consulta \
  -H "Content-Type: application/json" \
  -d '{"pregunta": "cual es el precio promedio de renta en mi zona"}'
```

## Environment variables

See `.env.example` for the required variable names. No real credentials are committed to this repository.

## Part of

[MBE Corp-Ai-Lot]([https://github.com/barbeltran-design/MBE-Corp-Ai-Lot]) — an AI-powered mentoring platform for small businesses and independent professionals.

## V2 — Recarga automática de tokens de IA (nuevo endpoint `/recargar-ia`)

Caso de uso para el jurado (Centrality + Autonomy): cuando el motor de IA de MBE
(Gemini → Groq → OpenRouter → DeepSeek) falla por saldo agotado (429 /
`sin_balance`), la app llama a este servicio y **Ecori decide y paga la recarga
de créditos del proveedor por sí mismo**, dentro de sus topes fijos
($1 USD por transacción, $5 USD por día). La app luego **reintenta** la llamada
al proveedor una sola vez y el usuario recibe su respuesta sin caídas.

```bash
curl -X POST http://localhost:8080/recargar-ia \
  -H "Content-Type: application/json" \
  -d '{"proveedor": "gemini", "estado": "quota_excedida", "pedido_id": "abc-123"}'
```

Respuesta (si recarga): `{"recarga_ejecutada": true, "proveedor": "gemini",
"monto_usd": 1.0, "detalle_pago": {...}, "explorer_url": "https://amoy.polygonscan.com/tx/..."}`

- La decisión es determinista por política (proveedor permitido + estado
  conocido + tope diario); se documenta que en producción puede sustituirse por
  razonamiento Gemini.
- El contador diario es en memoria para el sprint; en producción vive en
  Firestore (`ia_fondos`, `recargas_ia`, `proveedores_saldo`).
- Deploy: Cloud Run — `gcloud run deploy ecori-agent --source . --region us-central1 --allow-unauthenticated` (la autenticación real la hace la ruta proxy de la app con un secret compartido).
- Env vars nuevas: `EXPLORER_URL_TEMPLATE` (plantilla del block explorer con `{tx}`).

