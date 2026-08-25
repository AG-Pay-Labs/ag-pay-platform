# ruff: noqa: E501

import secrets
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="AG Pay local direct-card checkout fixture",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health/live")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/checkout", response_class=HTMLResponse)
async def checkout() -> HTMLResponse:
    """Render a no-charge form whose submit handler never sends card data."""
    nonce = secrets.token_urlsafe(18)
    current_year = datetime.now(UTC).year
    months = "".join(f"<option value='{month:02d}'>{month:02d}</option>" for month in range(1, 13))
    years = "".join(
        f"<option value='{year}'>{year}</option>" for year in range(current_year, current_year + 16)
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AG Pay direct-card research fixture</title>
<style>body{{font:16px system-ui;max-width:720px;margin:40px auto;padding:24px;color:#172033}}
.card{{border:1px solid #d8deea;border-radius:16px;padding:28px;box-shadow:0 12px 40px #17203312}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .wide{{grid-column:1/-1}}
label{{display:grid;gap:6px;font-weight:600}} input,select{{font:inherit;padding:11px;border:1px solid #aeb9cc;border-radius:8px}}
button{{background:#172033;color:white;border:0;border-radius:8px;padding:13px 18px;font-weight:700}}
#research-success{{color:#067647;font-weight:700}}</style></head>
<body><main class="card"><p>Local research fixture · no processor · no charge</p>
<h1 id="research-product-title">AG Pay direct-card research fixture</h1>
<p>Quantity: <output id="research-quantity">1</output></p>
<h2 id="research-total">Total EUR 25.00</h2>
<form id="research-payment-form" class="grid">
<label class="wide">Cardholder name<input id="research-cardholder" name="cardholder" autocomplete="cc-name" required></label>
<label class="wide">Card number<input id="research-card-number" name="card_number" autocomplete="cc-number" inputmode="numeric" required></label>
<label>Expiry month<select id="research-expiry-month" name="expiry_month" autocomplete="cc-exp-month" required><option value="">Month</option>{months}</select></label>
<label>Expiry year<select id="research-expiry-year" name="expiry_year" autocomplete="cc-exp-year" required><option value="">Year</option>{years}</select></label>
<label>CVC<input id="research-cvc" name="cvc" autocomplete="cc-csc" inputmode="numeric" required></label>
<label>Email<input id="research-email" name="email" type="email" autocomplete="email" required></label>
<label>Phone<input id="research-phone" name="phone" autocomplete="tel" required></label>
<label>Country<select id="research-country" name="country" autocomplete="country" required><option value="ES">Spain</option><option value="US">United States</option></select></label>
<label class="wide">Address<input id="research-line1" name="address_line1" autocomplete="address-line1" required></label>
<label>City<input id="research-city" name="city" autocomplete="address-level2" required></label>
<label>Region<input id="research-region" name="region" autocomplete="address-level1" required></label>
<label>Postal code<input id="research-postal-code" name="postal_code" autocomplete="postal-code" required></label>
<div class="wide"><button id="research-submit" type="submit">Complete no-charge fixture</button></div>
</form>
<p id="research-success" hidden>Fixture submission observed. No payment was attempted.</p>
<p>Order reference: <output id="research-order-reference"></output></p>
<script nonce="{nonce}">document.querySelector("#research-payment-form").addEventListener("submit",(event)=>{{
event.preventDefault();event.currentTarget.reset();event.currentTarget.hidden=true;
document.querySelector("#research-order-reference").textContent=`fixture-${{crypto.randomUUID()}}`;
document.querySelector("#research-success").hidden=false;
}});</script></main></body></html>"""
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; "
                f"script-src 'nonce-{nonce}'; "
                "style-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; "
                "img-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )
