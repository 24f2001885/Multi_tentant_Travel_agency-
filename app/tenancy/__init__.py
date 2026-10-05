"""
Package surface for tenant resolution and query scoping (TDR v2 §2, §6).
"""

from app.tenancy.resolver import init_tenancy
from app.tenancy.scoping import (
    TenantContextMissingError,
    TenantMismatchError,
    TenantScopedMixin,
    tenant_bypass,
    tenant_context,
)

# print("[DEBUG] app.tenancy loaded")

__all__ = [
    "init_tenancy",
    "TenantScopedMixin",
    "tenant_context",
    "tenant_bypass",
    "TenantContextMissingError",
    "TenantMismatchError",
]
