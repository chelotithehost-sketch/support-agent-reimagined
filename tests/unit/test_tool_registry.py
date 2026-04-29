"""Unit tests for the tool registry."""

from __future__ import annotations

import pytest

from afriagent.tools.registry import (
    TOOL_REGISTRY,
    get_tool_info,
    get_all_tools,
    get_tools_by_latency,
)


class TestToolRegistry:
    def test_registry_has_required_tools(self):
        required = ["check_invoice", "mpesa_push", "check_domain_dns", "create_support_ticket", "lookup_customer"]
        for tool in required:
            assert tool in TOOL_REGISTRY, f"Missing tool: {tool}"

    def test_all_tools_have_required_fields(self):
        required_fields = ["description", "requires", "returns", "latency_profile", "cost", "failure_mode"]
        for name, info in TOOL_REGISTRY.items():
            for field in required_fields:
                assert field in info, f"Tool '{name}' missing field: {field}"

    def test_latency_profiles_are_valid(self):
        valid_profiles = {"fast", "medium", "slow"}
        for name, info in TOOL_REGISTRY.items():
            assert info["latency_profile"] in valid_profiles, \
                f"Tool '{name}' has invalid latency_profile: {info['latency_profile']}"

    def test_all_tools_are_free(self):
        for name, info in TOOL_REGISTRY.items():
            assert info["cost"] == "free", f"Tool '{name}' is not free: {info['cost']}"


class TestGetToolInfo:
    def test_existing_tool(self):
        info = get_tool_info("check_invoice")
        assert info is not None
        assert "description" in info

    def test_nonexistent_tool(self):
        info = get_tool_info("nonexistent_tool")
        assert info is None


class TestGetAllTools:
    def test_returns_copy(self):
        tools = get_all_tools()
        tools["new_tool"] = {"description": "test"}
        assert "new_tool" not in TOOL_REGISTRY

    def test_returns_all_tools(self):
        tools = get_all_tools()
        assert len(tools) == len(TOOL_REGISTRY)


class TestGetToolsByLatency:
    def test_fast_tools(self):
        fast = get_tools_by_latency("fast")
        for name in fast:
            assert TOOL_REGISTRY[name]["latency_profile"] == "fast"

    def test_medium_tools(self):
        medium = get_tools_by_latency("medium")
        for name in medium:
            assert TOOL_REGISTRY[name]["latency_profile"] == "medium"

    def test_invalid_profile(self):
        result = get_tools_by_latency("instant")
        assert result == []


class TestDNSChecker:
    @pytest.mark.asyncio
    async def test_check_domain_returns_result(self):
        from afriagent.tools.dns_check import DNSChecker
        checker = DNSChecker()
        result = await checker.check_domain("google.com")
        assert "domain" in result
        assert "dns_records" in result
        assert "propagation_status" in result
        assert result["domain"] == "google.com"

    @pytest.mark.asyncio
    async def test_check_domain_strips_protocol(self):
        from afriagent.tools.dns_check import DNSChecker
        checker = DNSChecker()
        result = await checker.check_domain("https://google.com/")
        assert result["domain"] == "google.com"

    @pytest.mark.asyncio
    async def test_check_domain_has_a_records(self):
        from afriagent.tools.dns_check import DNSChecker
        checker = DNSChecker()
        result = await checker.check_domain("google.com")
        # google.com should have A records
        a_records = result["dns_records"].get("A", [])
        assert len(a_records) > 0

    def test_singleton(self):
        from afriagent.tools.dns_check import get_dns_checker
        c1 = get_dns_checker()
        c2 = get_dns_checker()
        assert c1 is c2
