from __future__ import annotations

from pathlib import Path

from netops_api.parsers.cisco_ios import parse_cisco_ios_config
from netops_api.parsers.cisco_ios.models import AssociationKind, LifetimeUnit
from netops_api.validators import validate_phase2_lifetime
from netops_api.validators.cisco_ios_ipsec import (
    PHASE2_LIFETIME_SECONDS_RULE_ID,
    PHASE2_LIFETIME_SECONDS_RULE_VERSION,
)
from netops_api.validators.models import ValidatorStatus

_FIXTURES = Path(__file__).parent / "fixtures"
_VERIFIED_WAN_MAP_ASSOCIATION = {"crypto_map:WAN-MAP:10": "crypto_map:WAN-MAP:10"}


def _configuration(name: str):
    return parse_cisco_ios_config((_FIXTURES / name).read_text(encoding="utf-8"))


def test_parser_preserves_lines_and_records_global_and_override_lifetimes() -> None:
    config = _configuration("phase2-local-override.cfg")

    assert config.source_lines[5].number == 6
    assert config.source_lines[5].text == " set security-association lifetime seconds 3600"
    assert config.global_lifetime(LifetimeUnit.SECONDS).value == 7200  # type: ignore[union-attr]

    association = config.ipsec_associations[0]
    assert association.kind is AssociationKind.CRYPTO_MAP
    assert association.identifier == "crypto_map:WAN-MAP:10"
    assert association.peer_addresses[0].line_number == 4
    override = association.effective_override(LifetimeUnit.SECONDS)
    assert override is not None
    assert override.line_number == 6


def test_phase2_lifetime_pass_includes_rule_version_and_exact_evidence() -> None:
    results = validate_phase2_lifetime(
        _configuration("phase2-local-override.cfg"),
        _configuration("phase2-peer-match.cfg"),
        peer_association_map=_VERIFIED_WAN_MAP_ASSOCIATION,
    )

    assert len(results) == 1
    result = results[0]
    assert result.rule_id == PHASE2_LIFETIME_SECONDS_RULE_ID
    assert result.rule_version == PHASE2_LIFETIME_SECONDS_RULE_VERSION
    assert result.status is ValidatorStatus.PASS
    assert result.observed_value == 3600
    assert result.expected_value == 3600
    assert [(item.document, item.line_number) for item in result.evidence] == [
        ("local", 6),
        ("peer", 4),
    ]


def test_phase2_lifetime_failure_reports_observed_expected_and_both_lines() -> None:
    results = validate_phase2_lifetime(
        _configuration("phase2-local-override.cfg"),
        _configuration("phase2-peer-mismatch.cfg"),
        peer_association_map=_VERIFIED_WAN_MAP_ASSOCIATION,
    )

    result = results[0]
    assert result.status is ValidatorStatus.FAIL
    assert result.observed_value == 3600
    assert result.expected_value == 2700
    assert [(item.document, item.line_number) for item in result.evidence] == [
        ("local", 6),
        ("peer", 4),
    ]


def test_phase2_lifetime_without_peer_is_insufficient_not_a_failure() -> None:
    results = validate_phase2_lifetime(_configuration("phase2-local-override.cfg"))

    result = results[0]
    assert result.status is ValidatorStatus.INSUFFICIENT_CONTEXT
    assert result.observed_value == 3600
    assert result.expected_value is None
    assert [(item.document, item.line_number) for item in result.evidence] == [("local", 6)]


def test_phase2_lifetime_uses_explicit_global_inheritance_with_peer_context() -> None:
    results = validate_phase2_lifetime(
        _configuration("phase2-inherited-local.cfg"),
        _configuration("phase2-inherited-peer.cfg"),
        peer_association_map=_VERIFIED_WAN_MAP_ASSOCIATION,
    )

    result = results[0]
    assert result.status is ValidatorStatus.PASS
    assert result.observed_value == 7200
    assert result.expected_value == 7200
    assert [(item.document, item.line_number) for item in result.evidence] == [
        ("local", 1),
        ("peer", 1),
    ]


def test_phase2_lifetime_does_not_infer_platform_default_when_not_declared() -> None:
    local = parse_cisco_ios_config("crypto map WAN-MAP 10 ipsec-isakmp\n set peer 198.51.100.10\n")
    peer = _configuration("phase2-peer-match.cfg")

    result = validate_phase2_lifetime(
        local,
        peer,
        peer_association_map=_VERIFIED_WAN_MAP_ASSOCIATION,
    )[0]

    assert result.status is ValidatorStatus.INSUFFICIENT_CONTEXT
    assert result.observed_value is None
    assert result.expected_value == 3600
    assert [(item.document, item.line_number) for item in result.evidence] == [("peer", 4)]


def test_malformed_lifetime_is_warned_and_never_used_as_evidence() -> None:
    config = parse_cisco_ios_config(
        "crypto map WAN-MAP 10 ipsec-isakmp\n set security-association lifetime seconds never\n"
    )

    assert config.warnings[0].line_number == 2
    assert config.ipsec_associations[0].lifetime_settings == ()


def test_matching_crypto_map_identifier_without_verified_association_is_insufficient() -> None:
    results = validate_phase2_lifetime(
        _configuration("phase2-identifier-collision-local.cfg"),
        _configuration("phase2-identifier-collision-peer.cfg"),
    )

    assert len(results) == 1
    result = results[0]
    assert result.status is ValidatorStatus.INSUFFICIENT_CONTEXT
    assert result.observed_value == 3600
    assert result.expected_value is None
    assert "explicit verified association" in result.explanation


def test_validator_status_contract_includes_warn_without_changing_result_semantics() -> None:
    assert ValidatorStatus.WARN.value == "warn"
    assert ValidatorStatus.PASS.value == "pass"
    assert ValidatorStatus.FAIL.value == "fail"
    assert ValidatorStatus.INSUFFICIENT_CONTEXT.value == "insufficient_context"
    assert ValidatorStatus.NOT_APPLICABLE.value == "not_applicable"
