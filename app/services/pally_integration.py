"""
Pally Integration — Add to your existing Pally FastAPI app.

Drop this into your Pally message handler to respond to
"toto", "toto picks", or "lottery" commands via WhatsApp.
"""

import httpx

from app.utils.config import settings

TOTO_API_URL = settings.toto_api_url


async def handle_toto_command(message: str) -> str:
    """Handle toto-related WhatsApp commands.

    Commands:
        "toto"          → Generate predictions (auto-fetch latest draw)
        "toto 4 11 15 16 21 39" → Predictions from specific draw
        "toto postmortem 4,11,15,16,21,39 vs 2,7,11,19,20,42"
    """
    msg = message.strip().lower()

    if msg in ("toto", "toto picks", "lottery"):
        return await _get_predictions()

    if msg.startswith("toto ") and "postmortem" not in msg:
        nums = [int(n) for n in msg.replace("toto", "").split() if n.isdigit()]
        if len(nums) == 6:
            return await _get_predictions(nums)

    return None  # Not a toto command


async def _get_predictions(last_draw: list[int] | None = None) -> str:
    """Call the Toto API and format for WhatsApp."""
    async with httpx.AsyncClient(timeout=float(settings.pally_api_timeout)) as client:
        if last_draw:
            resp = await client.post(
                f"{TOTO_API_URL}/predict",
                json={"numbers": last_draw},
            )
        else:
            resp = await client.get(f"{TOTO_API_URL}/predict")

        if resp.status_code != 200:
            return "⚠️ Could not generate predictions. Try again later."

        data = resp.json()

    lines = data["lines"]
    last = data["last_draw"]

    # Format for WhatsApp
    msg_parts = [
        f"🎱 *Toto Picks*",
        f"Based on: {last}",
        "",
    ]

    for line in lines:
        nums = line["numbers"]
        strategy = line["strategy"]
        tag = ""
        if strategy == "concentrated":
            tag = " ⭐"
        elif strategy == "low_skew":
            tag = " 📉"

        msg_parts.append(
            f"Line {line['line_number']}: *{nums}*{tag}\n"
            f"  Sum={line['sum_total']} Near={line['near_prev_count']} "
            f"{'✅Comp' if line['has_complement'] else ''}"
        )

    msg_parts.extend([
        "",
        f"Coverage: {data['coverage']}/49 numbers",
        "⭐=Concentrated 📉=Low-skew",
        "_Odds: 1 in 13,983,816 per line_",
    ])

    return "\n".join(msg_parts)


# ── Example: plug into your existing Pally handler ──

# In your Pally message handler (e.g., webhook endpoint):
#
# @app.post("/webhook/whatsapp")
# async def whatsapp_webhook(request: Request):
#     body = await request.json()
#     message = extract_message(body)
#
#     # Check toto command first
#     toto_response = await handle_toto_command(message)
#     if toto_response:
#         await send_whatsapp(toto_response)
#         return {"status": "ok"}
#
#     # ... rest of your Pally logic
