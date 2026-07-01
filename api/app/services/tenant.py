from __future__ import annotations

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.core.config import settings
from api.app.models.organization import Organization

_RESERVED_SUBDOMAINS = {"api", "admin", "www", "localhost"}


def is_admin_subdomain(request: Request) -> bool:
    host = _request_host(request)
    if not host:
        return False

    root_domain = settings.server_name.strip().lower().lstrip(".")
    if root_domain:
        return host == f"admin.{root_domain}"

    labels = host.split(".")
    return len(labels) >= 3 and labels[0] == "admin"


def extract_subdomain(request: Request) -> str | None:
    explicit = (
        request.headers.get("x-hospital-subdomain")
        or request.headers.get("x-tenant-subdomain")
        or request.headers.get("x-organization-subdomain")
    )
    if explicit:
        return _normalize_subdomain(explicit)

    host = _request_host(request)
    if not host or host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return None

    root_domain = settings.server_name.strip().lower().lstrip(".")
    if root_domain and host.endswith(f".{root_domain}"):
        return _normalize_subdomain(host[: -(len(root_domain) + 1)])

    labels = host.split(".")
    if len(labels) >= 3:
        return _normalize_subdomain(labels[0])
    return None


def _request_host(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0]
    host = forwarded_host or request.headers.get("host", "")
    return host.split(":", 1)[0].strip().lower()


def _normalize_subdomain(value: str) -> str | None:
    subdomain = value.strip().lower()
    if not subdomain or subdomain in _RESERVED_SUBDOMAINS:
        return None
    return subdomain


async def resolve_organization_from_subdomain(
    request: Request,
    session: AsyncSession,
    *,
    required: bool = False,
    org_type: str | None = None,
) -> Organization | None:
    subdomain = extract_subdomain(request)
    if not subdomain:
        if required:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant subdomain is required",
            )
        return None

    conditions = [
        Organization.subdomain == subdomain,
        Organization.is_active.is_(True),
    ]
    if org_type is not None:
        conditions.append(Organization.type == org_type)

    org = await session.scalar(select(Organization).where(*conditions))
    if org is None and required:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant subdomain was not found",
        )
    return org
