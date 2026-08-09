# ruff: noqa: E501

import html
from functools import lru_cache
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ag_platform_api.core.config import DemoMerchantSettings


class IntentRequest(BaseModel):
    execution_id: UUID


@lru_cache
def get_demo_settings() -> DemoMerchantSettings:
    return DemoMerchantSettings()


app = FastAPI(title="AG Pay Stripe Browserbase Demo Merchant", docs_url=None, redoc_url=None)


@app.get("/health/live")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/product", response_class=HTMLResponse)
async def product() -> HTMLResponse:
    settings = get_demo_settings()
    amount = settings.demo_amount_minor / 100
    return HTMLResponse(
        "<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width'><title>AG Pay demo product</title>"
        "<body style='font:16px system-ui;max-width:620px;margin:48px auto;padding:24px'>"
        f"<h1>{html.escape(settings.demo_product_title)}</h1>"
        "<p>A fixed Stripe test-mode product for the OpenClaw → AG Pay → Browserbase demo.</p>"
        f"<strong>{html.escape(settings.demo_currency)} {amount:.2f}</strong>"
        "<p>Quantity: 1</p></body></html>",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/payment-intents")
async def create_payment_intent(payload: IntentRequest) -> dict[str, str]:
    settings = get_demo_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.stripe_api_url.rstrip('/')}/v1/payment_intents",
                headers={
                    "Authorization": (
                        f"Bearer {settings.stripe_demo_secret_key.get_secret_value()}"
                    ),
                    "Idempotency-Key": f"agpay-demo-{payload.execution_id}",
                },
                data={
                    "amount": str(settings.demo_amount_minor),
                    "currency": settings.demo_currency.lower(),
                    "payment_method_types[]": "card",
                    "metadata[agpay_execution_id]": str(payload.execution_id),
                },
            )
            response.raise_for_status()
            intent = response.json()
        intent_id = str(intent["id"])
        client_secret = str(intent["client_secret"])
        if not intent_id.startswith("pi_") or not client_secret.startswith(f"{intent_id}_secret_"):
            raise ValueError
        return {"id": intent_id, "client_secret": client_secret}
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=502, detail="Stripe test payment could not be prepared"
        ) from None


@app.get("/checkout", response_class=HTMLResponse)
async def checkout(agpay_execution_id: UUID) -> HTMLResponse:
    settings = get_demo_settings()
    amount = settings.demo_amount_minor / 100
    publishable_key = html.escape(settings.stripe_demo_publishable_key, quote=True)
    title = html.escape(settings.demo_product_title)
    currency = html.escape(settings.demo_currency)
    execution_id = html.escape(str(agpay_execution_id), quote=True)
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AG Pay Browserbase demo checkout</title>
<script src="https://js.stripe.com/v3/"></script>
<style>body{{font:16px system-ui;max-width:620px;margin:48px auto;padding:24px;color:#172033}}
.card{{border:1px solid #d8deea;border-radius:16px;padding:28px;box-shadow:0 12px 40px #17203312}}
#card-element{{border:1px solid #b8c2d6;border-radius:8px;padding:14px;margin:20px 0}}
button{{background:#172033;color:white;border:0;border-radius:8px;padding:13px 18px;font-weight:700}}
[data-payment-failed]{{color:#b42318}} [data-order-confirmed]{{color:#067647}}</style></head>
<body><main class="card"><p>Stripe test mode · Browserbase</p>
<h1 data-checkout-product-title>{title}</h1>
<p>Quantity: <span data-checkout-quantity>1</span></p>
<h2 data-checkout-total>{currency} {amount:.2f}</h2>
<form id="payment-form"><div id="card-element"></div><button id="submit" type="submit">Pay in test mode</button></form>
<p data-payment-failed hidden>Payment declined by the Stripe test card.</p>
<p data-action-required hidden>Payment requires additional authentication.</p>
<p data-order-confirmed hidden>Payment succeeded in Stripe test mode.</p>
<p data-order-reference hidden></p>
<script>
const executionId={execution_id!r}; const stripe=Stripe({publishable_key!r});
const card=stripe.elements().create('card',{{hidePostalCode:true}}); card.mount('#card-element');
let prepared;
async function prepare(){{
 const response=await fetch('/payment-intents',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{execution_id:executionId}})}});
 if(!response.ok) throw new Error('prepare failed'); return response.json();
}}
prepared=prepare();
document.querySelector('#payment-form').addEventListener('submit',async(event)=>{{event.preventDefault();
 const intent=await prepared; document.querySelector('[data-order-reference]').textContent=intent.id;
 const result=await stripe.confirmCardPayment(intent.client_secret,{{payment_method:{{card}}}},{{handleActions:false}});
 if(result.error){{document.querySelector('[data-payment-failed]').hidden=false;document.querySelector('[data-order-reference]').hidden=false;return;}}
 if(result.paymentIntent.status==='succeeded'){{document.querySelector('[data-order-confirmed]').hidden=false;document.querySelector('[data-order-reference]').hidden=false;return;}}
 document.querySelector('[data-action-required]').hidden=false;document.querySelector('[data-order-reference]').hidden=false;
}});
</script></main></body></html>"""
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://js.stripe.com; "
                "frame-src https://js.stripe.com https://hooks.stripe.com; "
                "connect-src 'self' https://api.stripe.com https://r.stripe.com "
                "https://m.stripe.com https://m.stripe.network https://q.stripe.com; "
                "style-src 'self' 'unsafe-inline'"
            ),
        },
    )
