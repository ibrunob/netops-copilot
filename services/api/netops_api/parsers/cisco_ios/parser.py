"""Pure, line-preserving parser for the initial Cisco IOS IPsec subset.

The parser intentionally does not contact a device, infer topology, or invoke a
model. Unsupported IOS commands remain in ``source_lines`` and are ignored by
this narrow IR until a deterministic rule needs them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from netops_api.parsers.cisco_ios.models import (
    AssociationKind,
    CiscoIosConfig,
    IpsecAssociation,
    LifetimeUnit,
    ParseWarning,
    PeerAddress,
    Phase2LifetimeSetting,
    SourceLine,
)

_PROFILE_DECLARATION = re.compile(r"^crypto\s+ipsec\s+profile\s+(?P<name>\S+)\s*$", re.IGNORECASE)
_CRYPTO_MAP_DECLARATION = re.compile(
    r"^crypto\s+(?P<dynamic>dynamic-)?map\s+(?P<name>\S+)\s+(?P<sequence>\d+)\b",
    re.IGNORECASE,
)
_GLOBAL_LIFETIME = re.compile(
    r"^crypto\s+ipsec\s+security-association\s+lifetime\s+"
    r"(?P<unit>seconds|kilobytes)\s+(?P<value>\S+)\s*$",
    re.IGNORECASE,
)
_SET_LIFETIME = re.compile(
    r"^set\s+security-association\s+lifetime\s+(?P<unit>seconds|kilobytes)\s+"
    r"(?P<value>\S+)\s*$",
    re.IGNORECASE,
)
_SET_PEER = re.compile(r"^set\s+peer\s+(?P<peer>\S+)", re.IGNORECASE)
_LIFETIME_PREFIX = re.compile(
    r"^(?:crypto\s+ipsec\s+)?(?:set\s+)?security-association\s+lifetime\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _AssociationBuilder:
    kind: AssociationKind
    name: str
    declaration_line: int
    sequence: int | None
    peer_addresses: list[PeerAddress] = field(default_factory=list)
    lifetime_settings: list[Phase2LifetimeSetting] = field(default_factory=list)

    def freeze(self) -> IpsecAssociation:
        return IpsecAssociation(
            kind=self.kind,
            name=self.name,
            declaration_line=self.declaration_line,
            sequence=self.sequence,
            peer_addresses=tuple(self.peer_addresses),
            lifetime_settings=tuple(self.lifetime_settings),
        )


def _to_source_line(number: int, raw_line: str) -> SourceLine:
    text = raw_line.rstrip("\r\n")
    without_indent = text.lstrip(" \t")
    return SourceLine(
        number=number,
        text=text,
        indentation=len(text) - len(without_indent),
        tokens=tuple(without_indent.split()),
    )


def _parse_lifetime(
    *,
    line: SourceLine,
    command: str,
    match: re.Match[str] | None,
    warnings: list[ParseWarning],
) -> Phase2LifetimeSetting | None:
    if match is None:
        warnings.append(
            ParseWarning(
                code="cisco_ios.ipsec.malformed_phase2_lifetime",
                message="IPsec Phase 2 lifetime command is malformed and was not applied.",
                line_number=line.number,
            )
        )
        return None

    value_text = match.group("value")
    try:
        value = int(value_text)
    except ValueError:
        value = 0

    if value <= 0:
        warnings.append(
            ParseWarning(
                code="cisco_ios.ipsec.invalid_phase2_lifetime",
                message=(
                    f"IPsec Phase 2 {match.group('unit').lower()} lifetime {value_text!r} "
                    f"in {command} must be a positive integer."
                ),
                line_number=line.number,
            )
        )
        return None

    return Phase2LifetimeSetting(
        unit=LifetimeUnit(match.group("unit").lower()),
        value=value,
        line_number=line.number,
    )


def _top_level_declaration(command: str, line_number: int) -> _AssociationBuilder | None:
    profile_match = _PROFILE_DECLARATION.fullmatch(command)
    if profile_match is not None:
        return _AssociationBuilder(
            kind=AssociationKind.IPSEC_PROFILE,
            name=profile_match.group("name"),
            declaration_line=line_number,
            sequence=None,
        )

    crypto_map_match = _CRYPTO_MAP_DECLARATION.match(command)
    if crypto_map_match is not None:
        return _AssociationBuilder(
            kind=AssociationKind.CRYPTO_MAP,
            name=crypto_map_match.group("name"),
            declaration_line=line_number,
            sequence=int(crypto_map_match.group("sequence")),
        )

    return None


def parse_cisco_ios_config(configuration: str) -> CiscoIosConfig:
    """Parse Cisco IOS IPsec lifetime declarations without side effects.

    Supported scope is the top-level global Phase 2 lifetime, ``crypto map`` and
    ``crypto dynamic-map`` blocks, and ``crypto ipsec profile`` blocks. Every
    input line remains addressable in the returned IR, including comments and
    unsupported commands.
    """

    source_lines = tuple(
        _to_source_line(number, raw_line)
        for number, raw_line in enumerate(configuration.splitlines(), start=1)
    )
    warnings: list[ParseWarning] = []
    global_lifetimes: list[Phase2LifetimeSetting] = []
    associations: list[IpsecAssociation] = []
    current: _AssociationBuilder | None = None

    for line in source_lines:
        command = line.text.strip()
        if not command or command.startswith("!"):
            continue

        if line.indentation == 0:
            if current is not None:
                associations.append(current.freeze())
                current = None

            global_match = _GLOBAL_LIFETIME.fullmatch(command)
            if global_match is not None or _LIFETIME_PREFIX.match(command) is not None:
                setting = _parse_lifetime(
                    line=line,
                    command="global configuration",
                    match=global_match,
                    warnings=warnings,
                )
                if setting is not None:
                    global_lifetimes.append(setting)
                continue

            current = _top_level_declaration(command, line.number)
            continue

        if current is None:
            continue

        lifetime_match = _SET_LIFETIME.fullmatch(command)
        if lifetime_match is not None or _LIFETIME_PREFIX.match(command) is not None:
            setting = _parse_lifetime(
                line=line,
                command=current.name,
                match=lifetime_match,
                warnings=warnings,
            )
            if setting is not None:
                current.lifetime_settings.append(setting)
            continue

        peer_match = _SET_PEER.match(command)
        if peer_match is not None:
            current.peer_addresses.append(
                PeerAddress(value=peer_match.group("peer"), line_number=line.number)
            )

    if current is not None:
        associations.append(current.freeze())

    return CiscoIosConfig(
        source_lines=source_lines,
        global_phase2_lifetimes=tuple(global_lifetimes),
        ipsec_associations=tuple(associations),
        warnings=tuple(warnings),
    )
