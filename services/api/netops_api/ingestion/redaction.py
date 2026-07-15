"""Deterministic redaction of Cisco-style configuration before it leaves a trust boundary."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class RedactionRule(StrEnum):
    """Stable rule IDs recorded with redacted derivatives and audit metadata."""

    CISCO_IKEV1_PRE_SHARED_KEY = "cisco.ikev1_pre_shared_key"
    CISCO_IKEV2_PRE_SHARED_KEY = "cisco.ikev2_pre_shared_key"
    CISCO_USERNAME_SECRET = "cisco.username_secret"
    CISCO_PASSWORD = "cisco.password"
    CISCO_SNMP_COMMUNITY = "cisco.snmp_community"
    PRIVATE_KEY_BLOCK = "credential.private_key_block"
    BEARER_TOKEN = "credential.bearer_token"
    API_KEY = "credential.api_key"


@dataclass(frozen=True, slots=True)
class RedactionRuleSummary:
    """Count-only evidence that a rule redacted one or more source lines."""

    rule_id: str
    line_count: int
    occurrence_count: int


@dataclass(frozen=True, slots=True)
class RedactionReport:
    """Safe report that never stores the original text or a captured secret value."""

    source_line_count: int
    redacted_line_count: int
    rules: tuple[RedactionRuleSummary, ...]

    def summary_for(self, rule: RedactionRule) -> RedactionRuleSummary | None:
        """Return the count summary for one rule, if it matched source text."""
        return next((summary for summary in self.rules if summary.rule_id == rule.value), None)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """A line-preserving redacted derivative and its content-safe audit report."""

    content: str
    report: RedactionReport


@dataclass(slots=True)
class _RuleCounter:
    line_numbers: set[int] = field(default_factory=set)
    occurrence_count: int = 0

    @property
    def line_count(self) -> int:
        return len(self.line_numbers)


@dataclass(frozen=True, slots=True)
class _InlineRule:
    rule: RedactionRule
    pattern: re.Pattern[str]


_SECRET = r'(?!(?:<redacted:))(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s\r\n]+)'

_INLINE_RULES: Final[tuple[_InlineRule, ...]] = (
    _InlineRule(
        RedactionRule.CISCO_IKEV1_PRE_SHARED_KEY,
        re.compile(
            rf"(?P<prefix>^\s*crypto\s+isakmp\s+key\s+(?:\d+\s+)?)"
            rf"(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.CISCO_IKEV2_PRE_SHARED_KEY,
        re.compile(
            rf"(?P<prefix>^\s*pre-shared-key(?:\s+(?:local|remote))?\s+(?:\d+\s+)?)"
            rf"(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.CISCO_USERNAME_SECRET,
        re.compile(
            rf"(?P<prefix>\busername\s+\S+.*?\b(?:password|secret)\s+(?:\d+\s+)?)"
            rf"(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.CISCO_SNMP_COMMUNITY,
        re.compile(
            rf"(?P<prefix>^\s*snmp-server\s+community\s+)(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.CISCO_PASSWORD,
        re.compile(
            rf"(?P<prefix>^\s*(?:enable(?:\s+algorithm-type\s+\S+)?\s+)?"
            rf"(?:password|secret)\s+(?:\d+\s+)?)"
            rf"(?P<secret>(?!(?:encryption|policy)\b){_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.CISCO_PASSWORD,
        re.compile(
            rf"(?P<prefix>^\s*neighbor\s+\S+\s+password\s+(?:\d+\s+)?)"
            rf"(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.CISCO_PASSWORD,
        re.compile(
            rf"(?P<prefix>\bpassword\s*(?:=|:)\s*)(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.BEARER_TOKEN,
        re.compile(
            rf"(?P<prefix>\b(?:proxy-)?authorization\s*:\s*bearer\s+)(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
    _InlineRule(
        RedactionRule.BEARER_TOKEN,
        re.compile(rf"(?P<prefix>^\s*bearer\s+)(?P<secret>{_SECRET})", re.IGNORECASE),
    ),
    _InlineRule(
        RedactionRule.API_KEY,
        re.compile(
            rf"(?P<prefix>\b(?:x[-_]api[-_]key|api[-_]key)\s*(?:=|:)\s*)(?P<secret>{_SECRET})",
            re.IGNORECASE,
        ),
    ),
)

_PRIVATE_KEY_BEGIN: Final[re.Pattern[str]] = re.compile(
    r"^\s*-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----\s*$",
    re.IGNORECASE,
)
_PRIVATE_KEY_END: Final[re.Pattern[str]] = re.compile(
    r"^\s*-----END(?: [A-Z0-9]+)? PRIVATE KEY-----\s*$",
    re.IGNORECASE,
)


def redact_cisco_config(source: str) -> RedactionResult:
    """Redact supported secrets while retaining original line count, order, and line endings.

    The function is intentionally deterministic and side-effect free. It is appropriate for
    producing a redacted derivative before retrieval, logging, embedding, or model use; raw
    input must remain in encrypted artifact storage outside those paths.
    """
    counters: dict[RedactionRule, _RuleCounter] = {}
    redacted_line_numbers: set[int] = set()
    private_key_open = False
    output: list[str] = []
    source_lines = source.splitlines(keepends=True)

    for line_number, source_line in enumerate(source_lines, start=1):
        body, ending = _split_line_ending(source_line)
        begins_private_key = _PRIVATE_KEY_BEGIN.match(body) is not None
        ends_private_key = _PRIVATE_KEY_END.match(body) is not None

        if private_key_open or begins_private_key:
            _record_match(
                counters,
                redacted_line_numbers,
                rule=RedactionRule.PRIVATE_KEY_BLOCK,
                line_number=line_number,
                occurrence_count=1 if begins_private_key else 0,
            )
            output.append(_redact_entire_line(body, RedactionRule.PRIVATE_KEY_BLOCK) + ending)
            private_key_open = not ends_private_key
            continue

        redacted_body = body
        for inline_rule in _INLINE_RULES:
            redacted_body, replacements = inline_rule.pattern.subn(
                _replacement_for(inline_rule.rule), redacted_body
            )
            if replacements:
                _record_match(
                    counters,
                    redacted_line_numbers,
                    rule=inline_rule.rule,
                    line_number=line_number,
                    occurrence_count=replacements,
                )
        output.append(redacted_body + ending)

    report = RedactionReport(
        source_line_count=len(source_lines),
        redacted_line_count=len(redacted_line_numbers),
        rules=tuple(
            RedactionRuleSummary(
                rule_id=rule.value,
                line_count=counters[rule].line_count,
                occurrence_count=counters[rule].occurrence_count,
            )
            for rule in sorted(counters, key=lambda item: item.value)
        ),
    )
    return RedactionResult(content="".join(output), report=report)


def _replacement_for(rule: RedactionRule) -> Callable[[re.Match[str]], str]:
    marker = f"<redacted:{rule.value}>"

    def replace(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{marker}"

    return replace


def _record_match(
    counters: dict[RedactionRule, _RuleCounter],
    redacted_line_numbers: set[int],
    *,
    rule: RedactionRule,
    line_number: int,
    occurrence_count: int,
) -> None:
    counter = counters.setdefault(rule, _RuleCounter())
    counter.line_numbers.add(line_number)
    counter.occurrence_count += occurrence_count
    redacted_line_numbers.add(line_number)


def _redact_entire_line(line: str, rule: RedactionRule) -> str:
    indentation = line[: len(line) - len(line.lstrip())]
    return f"{indentation}<redacted:{rule.value}>"


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""
