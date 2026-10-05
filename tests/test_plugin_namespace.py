"""Tests for plugin tool namespacing.

Each test names an invariant, not a line number. No mocks: these are
deterministic pure functions, and a mock would only hide the substring bug.

Run red-first: with the production code unpatched, the first three tests
fail and the last two fail too (namespacing is inert end-to-end).
"""

import pytest

from wisp.plugins.namespace import NamespaceManager


class TestResolveRejectsForgedPrefixes:
    """A tool name must only resolve to a namespace that actually exists."""

    def test_unknown_namespace_resolves_as_core_tool(self):
        ns = NamespaceManager()
        assert ns.resolve_tool_name("nosuch__tool") == (None, "nosuch__tool")

    def test_substring_of_real_namespace_is_not_a_namespace(self):
        """'my' is a substring of 'my__org' but is not that namespace.

        Guards the ``in``-on-string substring test at the resolve site.
        """
        ns = NamespaceManager()
        key = ns.prefix_tool_name("my__org", "tool")
        assert ns.resolve_tool_name(key) == (None, key)

    def test_ambiguous_tool_name_is_not_silently_split(self):
        """'a__b__tool' must not resolve to ('a', 'b__tool')."""
        ns = NamespaceManager()
        key = ns.prefix_tool_name("a__b", "tool")
        assert ns.resolve_tool_name(key) == (None, key)

    def test_reserved_prefix_attack_after_registration(self):
        """Once 'read' is a real namespace, 'read__tool' must resolve to it."""
        ns = NamespaceManager()
        ns.register_plugin_namespace("acme")
        ns.prefix_tool_name("acme", "helper")
        assert ns.resolve_tool_name("acme__helper") == ("acme", "helper")


class TestNamespaceRegistration:
    """register_plugin_namespace must actually record, and enforce uniqueness."""

    def test_registration_makes_namespace_known(self):
        ns = NamespaceManager()
        ns.register_plugin_namespace("acme")
        assert ns._is_known_namespace("acme") is True

    def test_duplicate_registration_is_rejected(self):
        ns = NamespaceManager()
        ns.register_plugin_namespace("acme")
        with pytest.raises(ValueError):
            ns.register_plugin_namespace("acme")

    def test_registration_rejects_reserved_namespace(self):
        ns = NamespaceManager()
        with pytest.raises(ValueError):
            ns.register_plugin_namespace("read")


class TestPrefixingNeverBorrowsCoreIdentity:
    """A plugin tool must never land unprefixed under a core name."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "search_files",
            "read_notes",
            "write_report",
            "bash_history",
            "grep_output",
            "todo_list",
        ],
    )
    def test_core_prefix_named_plugin_tool_is_still_prefixed(self, tool_name):
        ns = NamespaceManager()
        assert ns.prefix_tool_name("acme", tool_name) == f"acme__{tool_name}"

    def test_non_core_looking_name_is_prefixed(self):
        ns = NamespaceManager()
        assert ns.prefix_tool_name("acme", "totally_custom") == "acme__totally_custom"

    def test_custom_reserved_set_is_honoured(self):
        ns = NamespaceManager(reserved_prefixes={"widget"})
        assert ns.prefix_tool_name("acme", "widget") == "widget"
        assert ns.prefix_tool_name("acme", "widget_gear") == "acme__widget_gear"
