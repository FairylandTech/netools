import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from netools.core.errors import ServiceError
from netools.ip_info.pool import IpregistryKeyPool
from netools.ip_risk.pool import ScamalyticsKeyPool
from netools.lookup.service import LookupService

bearer = HTTPBearer(auto_error=False)


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    expected = request.app.state.settings.service_api_token.get_secret_value()
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise ServiceError("unauthorized", "A valid Bearer token is required", 401)


protected = [Depends(authenticate)]


def get_lookup(request: Request) -> LookupService:
    return request.app.state.lookup


def get_risk_pool(request: Request) -> ScamalyticsKeyPool:
    return request.app.state.risk_pool


def get_info_pool(request: Request) -> IpregistryKeyPool:
    return request.app.state.info_pool


Lookup = Annotated[LookupService, Depends(get_lookup)]
RiskPool = Annotated[ScamalyticsKeyPool, Depends(get_risk_pool)]
InfoPool = Annotated[IpregistryKeyPool, Depends(get_info_pool)]
