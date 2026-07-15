"""Cisco IOS parser surface for deterministic validators."""

from netops_api.parsers.cisco_ios.models import CiscoIosConfig
from netops_api.parsers.cisco_ios.parser import parse_cisco_ios_config

__all__ = ["CiscoIosConfig", "parse_cisco_ios_config"]
