"""Tests for shared helper functions."""

from monarch_mcp_server.helpers import format_transaction, tool_response_envelope


class TestFormatTransaction:
    def test_tolerates_non_dict_nested_fields(self):
        """A truthy non-dict nested field must not raise AttributeError."""
        txn = {
            "id": "t1",
            "amount": -5.0,
            "merchant": "just a string",  # not a dict
            "category": ["unexpected"],  # not a dict
            "account": None,
            "tags": "nope",  # not a list
        }
        info = format_transaction(txn)
        assert info["merchant"] is None
        assert info["category"] is None
        assert info["account"] is None
        assert info["tags"] == []

    def test_skips_non_dict_tags(self):
        txn = {"id": "t1", "tags": [{"id": "g1", "name": "A"}, "bad", None]}
        info = format_transaction(txn)
        assert info["tags"] == [{"id": "g1", "name": "A"}]


class TestToolResponseEnvelope:
    def test_coerces_string_pagination_args(self):
        """String-typed offset/limit must not raise inside the envelope."""
        rows = [{"id": "1"}, {"id": "2"}]
        env = tool_response_envelope(
            "t", {"limit": "2", "offset": "0"}, rows
        )
        assert env["count"] == 2
        # count == limit (coerced) and total unknown → truncated.
        assert env["truncated"] is True

    def test_truncated_false_when_total_known_and_covered(self):
        rows = [{"id": "1"}, {"id": "2"}]
        env = tool_response_envelope("t", {"limit": 5, "offset": 0}, rows, total_count=2)
        assert env["truncated"] is False
