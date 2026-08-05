from fastapi import APIRouter

from ag_platform_api.api.routes import (
    agent_api,
    agents,
    auth,
    cart,
    payment_methods,
    payment_policies,
    purchases,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(agents.router)
api_router.include_router(payment_methods.router)
api_router.include_router(payment_policies.router)
api_router.include_router(cart.router)
api_router.include_router(purchases.router)
api_router.include_router(agent_api.router)
