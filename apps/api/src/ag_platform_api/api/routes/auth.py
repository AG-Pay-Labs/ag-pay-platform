from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ag_platform_api.api.dependencies import AppSettings, CurrentUser, DatabaseSession
from ag_platform_api.core.security import create_access_token, hash_password, verify_password
from ag_platform_api.models import User
from ag_platform_api.schemas import LoginRequest, TokenResponse, UserRead, UserRegister

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserRegister,
    db: DatabaseSession,
    settings: AppSettings,
) -> TokenResponse:
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Username is already registered") from exc
    await db.refresh(user)
    token, expires_at = create_access_token(str(user.id), settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: DatabaseSession,
    settings: AppSettings,
) -> TokenResponse:
    user = await db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password.get_secret_value(), user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")
    token, expires_at = create_access_token(str(user.id), settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> User:
    return user
