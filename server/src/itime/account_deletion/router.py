from fastapi import APIRouter, Request, Response

from itime.account_deletion.schemas import AccountDeletionRequest
from itime.account_deletion.service import request_account_deletion
from itime.auth.security import COOKIE_NAME, rate_limit
from itime.auth.session import authorized

router = APIRouter(
    prefix="/api/v1/account-deletion",
    tags=["账号注销"],
)


@router.post("", status_code=202)
async def request_deletion(
    data: AccountDeletionRequest,
    request: Request,
    response: Response,
) -> dict:
    async with authorized(request, write=True) as identity:
        user_id = identity.user.id
        password_hash = identity.user.password_hash
    await rate_limit(request, "account-deletion", str(user_id))
    result = await request_account_deletion(
        request,
        user_id=user_id,
        password_hash=password_hash,
        data=data,
    )
    response.delete_cookie(
        COOKIE_NAME,
        path="/api",
        httponly=True,
        samesite="lax",
    )
    return result
