from __future__ import annotations

from netops_api.ingestion.redaction import RedactionRule, redact_cisco_config


def test_redacts_supported_inline_secrets_and_keeps_source_lines() -> None:
    source = "\n".join(
        (
            "hostname edge-01",
            'crypto isakmp key "ikev1-sensitive" address 198.51.100.10',
            " pre-shared-key local ikev2-sensitive",
            "enable secret 5 enable-sensitive-hash",
            "username netops privilege 15 secret 9 username-sensitive-hash",
            "snmp-server community snmp-sensitive RO 198.51.100.0 0.0.0.255",
            "neighbor 192.0.2.1 password neighbor-sensitive",
            "password: line-sensitive",
            "Authorization: Bearer bearer-sensitive-token",
            "x-api-key: api-sensitive-token",
            "interface Tunnel0",
        )
    )

    result = redact_cisco_config(source)

    for secret in (
        "ikev1-sensitive",
        "ikev2-sensitive",
        "enable-sensitive-hash",
        "username-sensitive-hash",
        "snmp-sensitive",
        "neighbor-sensitive",
        "line-sensitive",
        "bearer-sensitive-token",
        "api-sensitive-token",
    ):
        assert secret not in result.content
        assert secret not in repr(result.report)

    assert result.content.splitlines()[0] == "hostname edge-01"
    assert result.content.splitlines()[-1] == "interface Tunnel0"
    assert len(result.content.splitlines()) == len(source.splitlines())
    assert result.report.source_line_count == 11
    assert result.report.redacted_line_count == 9
    assert result.report.summary_for(RedactionRule.CISCO_IKEV1_PRE_SHARED_KEY) is not None
    assert result.report.summary_for(RedactionRule.CISCO_IKEV2_PRE_SHARED_KEY) is not None
    assert result.report.summary_for(RedactionRule.CISCO_USERNAME_SECRET) is not None
    assert result.report.summary_for(RedactionRule.CISCO_PASSWORD).occurrence_count == 3
    assert result.report.summary_for(RedactionRule.CISCO_SNMP_COMMUNITY) is not None
    assert result.report.summary_for(RedactionRule.BEARER_TOKEN) is not None
    assert result.report.summary_for(RedactionRule.API_KEY) is not None


def test_redacts_enable_algorithm_type_and_adjacent_enable_secret_forms() -> None:
    source = "\n".join(
        (
            "enable secret 9 legacy-enable-secret",
            "enable algorithm-type scrypt secret scrypt-enable-secret",
            "enable algorithm-type pbkdf2 secret 9 pbkdf2-enable-secret",
        )
    )

    result = redact_cisco_config(source)
    summary = result.report.summary_for(RedactionRule.CISCO_PASSWORD)

    for secret in (
        "legacy-enable-secret",
        "scrypt-enable-secret",
        "pbkdf2-enable-secret",
    ):
        assert secret not in result.content
        assert secret not in repr(result.report)
    assert len(result.content.splitlines()) == len(source.splitlines())
    assert summary is not None
    assert summary.line_count == 3
    assert summary.occurrence_count == 3


def test_redacts_private_key_block_without_changing_line_count_or_endings() -> None:
    source = (
        "crypto pki trustpoint NETOPS\r\n"
        " -----BEGIN RSA PRIVATE KEY-----\r\n"
        " very-sensitive-base64-material\r\n"
        " -----END RSA PRIVATE KEY-----\r\n"
        "end\r\n"
    )

    result = redact_cisco_config(source)
    summary = result.report.summary_for(RedactionRule.PRIVATE_KEY_BLOCK)

    assert "very-sensitive-base64-material" not in result.content
    assert result.content.count("<redacted:credential.private_key_block>") == 3
    assert result.content.splitlines(keepends=True)[0] == "crypto pki trustpoint NETOPS\r\n"
    assert len(result.content.splitlines(keepends=True)) == len(source.splitlines(keepends=True))
    assert summary is not None
    assert summary.line_count == 3
    assert summary.occurrence_count == 1


def test_report_counts_multiple_occurrences_on_one_line_without_secret_material() -> None:
    source = "x-api-key: first-secret api_key=second-secret\n"

    result = redact_cisco_config(source)
    summary = result.report.summary_for(RedactionRule.API_KEY)

    assert "first-secret" not in result.content
    assert "second-secret" not in result.content
    assert summary is not None
    assert summary.line_count == 1
    assert summary.occurrence_count == 2
    assert result.report.redacted_line_count == 1


def test_false_positive_guarding_leaves_non_secret_config_and_prose_unchanged() -> None:
    source = "\n".join(
        (
            "description pre-shared-key rotation is managed externally",
            "password encryption aes",
            "description password policy requires an approver",
            "Authorization profile requires Bearer authentication",
            "api-key rotation is tracked by the platform team",
            "username observer privilege 1",
            "snmp-server host 192.0.2.10 version 3 auth observer",
        )
    )

    result = redact_cisco_config(source)

    assert result.content == source
    assert result.report.redacted_line_count == 0
    assert result.report.rules == ()


def test_redaction_is_idempotent_and_handles_empty_input() -> None:
    first = redact_cisco_config("Bearer token-value\n")
    second = redact_cisco_config(first.content)
    empty = redact_cisco_config("")

    assert second.content == first.content
    assert second.report.redacted_line_count == 0
    assert empty.content == ""
    assert empty.report.source_line_count == 0
