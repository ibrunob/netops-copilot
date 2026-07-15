"""Offline Cisco IOS IPsec Phase 2 validators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from netops_api.parsers.cisco_ios.models import (
    CiscoIosConfig,
    IpsecAssociation,
    LifetimeUnit,
    Phase2LifetimeSetting,
)
from netops_api.validators.models import (
    EvidenceLocation,
    FindingSeverity,
    ValidatorResult,
    ValidatorStatus,
)

PHASE2_LIFETIME_SECONDS_RULE_ID = "cisco_ios.ipsec.phase2_lifetime_seconds"
PHASE2_LIFETIME_SECONDS_RULE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class _EffectiveLifetime:
    value: int | None
    source: Phase2LifetimeSetting | None


def _effective_lifetime(
    configuration: CiscoIosConfig,
    association: IpsecAssociation,
    unit: LifetimeUnit,
) -> _EffectiveLifetime:
    override = association.effective_override(unit)
    if override is not None:
        return _EffectiveLifetime(value=override.value, source=override)

    inherited = configuration.global_lifetime(unit)
    if inherited is not None:
        return _EffectiveLifetime(value=inherited.value, source=inherited)

    return _EffectiveLifetime(value=None, source=None)


def _evidence(
    *,
    local: _EffectiveLifetime,
    peer: _EffectiveLifetime | None = None,
) -> tuple[EvidenceLocation, ...]:
    evidence: list[EvidenceLocation] = []
    if local.source is not None:
        evidence.append(
            EvidenceLocation(
                document="local",
                line_number=local.source.line_number,
                role="observed_phase2_lifetime",
            )
        )
    if peer is not None and peer.source is not None:
        evidence.append(
            EvidenceLocation(
                document="peer",
                line_number=peer.source.line_number,
                role="expected_phase2_lifetime",
            )
        )
    return tuple(evidence)


def _result(
    *,
    status: ValidatorStatus,
    severity: FindingSeverity,
    association: IpsecAssociation | None,
    local: _EffectiveLifetime,
    peer: _EffectiveLifetime | None,
    explanation: str,
) -> ValidatorResult:
    return ValidatorResult(
        rule_id=PHASE2_LIFETIME_SECONDS_RULE_ID,
        rule_version=PHASE2_LIFETIME_SECONDS_RULE_VERSION,
        status=status,
        severity=severity,
        association_id=None if association is None else association.identifier,
        unit=LifetimeUnit.SECONDS,
        observed_value=local.value,
        expected_value=None if peer is None else peer.value,
        evidence=_evidence(local=local, peer=peer),
        explanation=explanation,
    )


def _peer_association(
    *,
    local_association: IpsecAssociation,
    peer_config: CiscoIosConfig,
    association_map: Mapping[str, str] | None,
) -> IpsecAssociation | None:
    """Resolve only a caller-supplied, previously verified association mapping.

    Crypto map and IPsec profile identifiers are local configuration labels, not remote
    identity proof. A matching identifier, or merely one association on each device, can
    collide across independent tunnels. The caller must first verify the relationship from
    reciprocal peer/interface topology evidence and supply that result in ``association_map``.
    """
    if association_map is None:
        return None

    peer_identifier = association_map.get(local_association.identifier)
    if peer_identifier is None:
        return None

    matches = tuple(
        association
        for association in peer_config.ipsec_associations
        if association.identifier == peer_identifier
    )
    return matches[0] if len(matches) == 1 else None


def validate_phase2_lifetime(
    local_config: CiscoIosConfig,
    peer_config: CiscoIosConfig | None = None,
    *,
    peer_association_map: Mapping[str, str] | None = None,
) -> tuple[ValidatorResult, ...]:
    """Validate IOS IPsec Phase 2 *seconds* lifetimes against supplied peer input.

    The function is deterministic and has no I/O. It does not infer a remote
    configuration, topology, or peer pairing. ``peer_association_map`` must contain
    associations that a caller has already verified from peer/topology evidence; matching
    crypto-map or profile names is never enough. When no peer configuration is supplied,
    or association pairing is absent or ambiguous, it returns
    ``insufficient_context`` rather than a mismatch. A per-association setting
    overrides an explicit global setting; platform defaults absent from input are
    also treated as insufficient context.
    """

    if not local_config.ipsec_associations:
        return (
            _result(
                status=ValidatorStatus.NOT_APPLICABLE,
                severity=FindingSeverity.INFO,
                association=None,
                local=_EffectiveLifetime(value=None, source=None),
                peer=None,
                explanation="No Cisco IOS IPsec crypto map or IPsec profile was found.",
            ),
        )

    results: list[ValidatorResult] = []
    for local_association in local_config.ipsec_associations:
        local_lifetime = _effective_lifetime(
            local_config,
            local_association,
            LifetimeUnit.SECONDS,
        )

        if peer_config is None:
            results.append(
                _result(
                    status=ValidatorStatus.INSUFFICIENT_CONTEXT,
                    severity=FindingSeverity.WARNING,
                    association=local_association,
                    local=local_lifetime,
                    peer=None,
                    explanation=(
                        "Peer configuration was not supplied; the local Phase 2 lifetime "
                        "cannot be compared."
                    ),
                )
            )
            continue

        peer_association = _peer_association(
            local_association=local_association,
            peer_config=peer_config,
            association_map=peer_association_map,
        )
        if peer_association is None:
            results.append(
                _result(
                    status=ValidatorStatus.INSUFFICIENT_CONTEXT,
                    severity=FindingSeverity.WARNING,
                    association=local_association,
                    local=local_lifetime,
                    peer=None,
                    explanation=(
                        "Peer configuration was supplied, but this IPsec association could not "
                        "be paired through an explicit verified association."
                    ),
                )
            )
            continue

        peer_lifetime = _effective_lifetime(
            peer_config,
            peer_association,
            LifetimeUnit.SECONDS,
        )
        if local_lifetime.value is None or peer_lifetime.value is None:
            results.append(
                _result(
                    status=ValidatorStatus.INSUFFICIENT_CONTEXT,
                    severity=FindingSeverity.WARNING,
                    association=local_association,
                    local=local_lifetime,
                    peer=peer_lifetime,
                    explanation=(
                        "An explicit Phase 2 seconds lifetime is missing on one side; IOS "
                        "platform defaults are not inferred by this validator."
                    ),
                )
            )
            continue

        if local_lifetime.value == peer_lifetime.value:
            results.append(
                _result(
                    status=ValidatorStatus.PASS,
                    severity=FindingSeverity.INFO,
                    association=local_association,
                    local=local_lifetime,
                    peer=peer_lifetime,
                    explanation="Local and peer Phase 2 seconds lifetimes match.",
                )
            )
            continue

        results.append(
            _result(
                status=ValidatorStatus.FAIL,
                severity=FindingSeverity.ERROR,
                association=local_association,
                local=local_lifetime,
                peer=peer_lifetime,
                explanation="Local and peer Phase 2 seconds lifetimes do not match.",
            )
        )

    return tuple(results)
