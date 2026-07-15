"""Line-preserving intermediate representation for the initial Cisco IOS scope."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LifetimeUnit(StrEnum):
    """Units Cisco IOS accepts for an IPsec Phase 2 SA lifetime."""

    SECONDS = "seconds"
    KILOBYTES = "kilobytes"


class AssociationKind(StrEnum):
    """IOS configuration forms that can define an IPsec Phase 2 lifetime."""

    CRYPTO_MAP = "crypto_map"
    IPSEC_PROFILE = "ipsec_profile"


@dataclass(frozen=True, slots=True)
class SourceLine:
    """A source line retained unchanged enough for exact evidence references."""

    number: int
    text: str
    indentation: int
    tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """A non-fatal parser warning tied to the original input location."""

    code: str
    message: str
    line_number: int


@dataclass(frozen=True, slots=True)
class Phase2LifetimeSetting:
    """An explicit lifetime command and the line that supplied it."""

    unit: LifetimeUnit
    value: int
    line_number: int


@dataclass(frozen=True, slots=True)
class PeerAddress:
    """A peer address declaration retained for future association matching."""

    value: str
    line_number: int


@dataclass(frozen=True, slots=True)
class IpsecAssociation:
    """A crypto map or IPsec profile that can override global Phase 2 settings."""

    kind: AssociationKind
    name: str
    declaration_line: int
    sequence: int | None
    peer_addresses: tuple[PeerAddress, ...]
    lifetime_settings: tuple[Phase2LifetimeSetting, ...]

    @property
    def identifier(self) -> str:
        """Stable within-config reference used for explicit peer association maps."""

        if self.sequence is None:
            return f"{self.kind}:{self.name}"
        return f"{self.kind}:{self.name}:{self.sequence}"

    def effective_override(self, unit: LifetimeUnit) -> Phase2LifetimeSetting | None:
        """Return the last setting in the block, matching IOS command replacement."""

        for setting in reversed(self.lifetime_settings):
            if setting.unit is unit:
                return setting
        return None


@dataclass(frozen=True, slots=True)
class CiscoIosConfig:
    """Typed parser output for the Cisco IOS IPsec configuration subset."""

    source_lines: tuple[SourceLine, ...]
    global_phase2_lifetimes: tuple[Phase2LifetimeSetting, ...]
    ipsec_associations: tuple[IpsecAssociation, ...]
    warnings: tuple[ParseWarning, ...]

    def global_lifetime(self, unit: LifetimeUnit) -> Phase2LifetimeSetting | None:
        """Return the last global setting for a unit, if the input declares one."""

        for setting in reversed(self.global_phase2_lifetimes):
            if setting.unit is unit:
                return setting
        return None
