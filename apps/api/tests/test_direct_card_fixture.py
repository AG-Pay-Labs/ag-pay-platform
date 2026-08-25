from httpx import ASGITransport, AsyncClient

from ag_platform_api.direct_card_fixture import app


async def test_direct_card_fixture_is_no_charge_and_network_closed() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://fixture.test",
    ) as client:
        response = await client.get("/checkout")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    policy = response.headers["content-security-policy"]
    assert "connect-src 'none'" in policy
    assert "form-action 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert 'id="research-card-number"' in response.text
    assert 'autocomplete="cc-number"' in response.text
    assert 'id="research-cvc"' in response.text
    assert 'autocomplete="cc-csc"' in response.text
    assert 'id="research-expiry-month"' in response.text
    assert 'id="research-submit"' in response.text
    assert 'id="research-success" hidden' in response.text
    assert 'id="research-order-reference"' in response.text
    assert "fetch(" not in response.text
    assert "XMLHttpRequest" not in response.text
    assert "form.submit" not in response.text
