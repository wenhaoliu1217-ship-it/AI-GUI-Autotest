"""Capability pack selection by target URL."""

from __future__ import annotations

from .base import GenericWebCapabilityPack, SiteCapabilityPack
from .cesium_ion import CesiumIonCapabilityPack
from .gaealavic import GAEALaViCCapabilityPack


_PACKS: tuple[SiteCapabilityPack, ...] = (CesiumIonCapabilityPack(), GAEALaViCCapabilityPack())
_GENERIC = GenericWebCapabilityPack()


def resolve_site_capability_pack(url: str) -> SiteCapabilityPack:
    return next((pack for pack in _PACKS if pack.matches(url)), _GENERIC)
