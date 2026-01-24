"""Tests for hybridgraph fixes - hash collision prevention and ref_count logic."""

import pytest
import sys
sys.path.insert(0, 'src')

from runner.utils.hashing import compute_content_hash, compute_merkle_hash, encode_value_for_hash


class TestHashCollisionPrevention:
    """Tests to verify hash collisions are prevented between types."""

    def test_hash_collision_prevention_bool_vs_string(self):
        """Verify that boolean true vs string 'true' produce different hashes."""
        bool_value = encode_value_for_hash("boolean", None, None, True)
        str_value = encode_value_for_hash("string", "true", None, None)

        bool_hash = compute_content_hash("boolean", "flag", bool_value)
        str_hash = compute_content_hash("string", "flag", str_value)

        assert bool_hash != str_hash, "Boolean and string 'true' should produce different hashes"

    def test_hash_collision_prevention_bool_false_vs_string(self):
        """Verify that boolean false vs string 'false' produce different hashes."""
        bool_value = encode_value_for_hash("boolean", None, None, False)
        str_value = encode_value_for_hash("string", "false", None, None)

        bool_hash = compute_content_hash("boolean", "flag", bool_value)
        str_hash = compute_content_hash("string", "flag", str_value)

        assert bool_hash != str_hash, "Boolean false and string 'false' should produce different hashes"

    def test_hash_collision_prevention_number_vs_string(self):
        """Verify that number 1.0 vs string '1.0' produce different hashes."""
        num_value = encode_value_for_hash("number", None, 1.0, None)
        str_num_value = encode_value_for_hash("string", "1.0", None, None)

        num_hash = compute_content_hash("number", "val", num_value)
        str_num_hash = compute_content_hash("string", "val", str_num_value)

        assert num_hash != str_num_hash, "Number and string '1.0' should produce different hashes"

    def test_hash_collision_prevention_integer_vs_string(self):
        """Verify that integer 42 vs string '42' produce different hashes."""
        num_value = encode_value_for_hash("number", None, 42, None)
        str_num_value = encode_value_for_hash("string", "42", None, None)

        num_hash = compute_content_hash("number", "val", num_value)
        str_num_hash = compute_content_hash("string", "val", str_num_value)

        assert num_hash != str_num_hash, "Number 42 and string '42' should produce different hashes"

    def test_hash_collision_prevention_null_vs_string(self):
        """Verify that null vs string 'null' produce different hashes."""
        null_value = encode_value_for_hash("null", None, None, None)
        str_null_value = encode_value_for_hash("string", "null", None, None)

        null_hash = compute_content_hash("null", "val", null_value)
        str_null_hash = compute_content_hash("string", "val", str_null_value)

        assert null_hash != str_null_hash, "Null and string 'null' should produce different hashes"

    def test_hash_collision_prevention_zero_vs_string(self):
        """Verify that number 0 vs string '0' produce different hashes."""
        num_value = encode_value_for_hash("number", None, 0, None)
        str_value = encode_value_for_hash("string", "0", None, None)

        num_hash = compute_content_hash("number", "val", num_value)
        str_hash = compute_content_hash("string", "val", str_value)

        assert num_hash != str_hash, "Number 0 and string '0' should produce different hashes"

    def test_hash_collision_prevention_empty_string_vs_null(self):
        """Verify that empty string vs null produce different hashes."""
        empty_value = encode_value_for_hash("string", "", None, None)
        null_value = encode_value_for_hash("null", None, None, None)

        empty_hash = compute_content_hash("string", "val", empty_value)
        null_hash = compute_content_hash("null", "val", null_value)

        assert empty_hash != null_hash, "Empty string and null should produce different hashes"


class TestHashFormat:
    """Tests to verify hash output format."""

    def test_content_hash_prefix(self):
        """Verify content hash starts with 'c:' prefix."""
        content_hash = compute_content_hash("string", "key", "value")
        assert content_hash.startswith("c:"), "Content hash should start with 'c:'"

    def test_content_hash_length(self):
        """Verify content hash is 34 chars (c: + 32 hex)."""
        content_hash = compute_content_hash("string", "key", "value")
        assert len(content_hash) == 34, "Content hash should be 34 chars (c: + 32 hex)"

    def test_content_hash_hex_characters(self):
        """Verify content hash contains only hex characters after prefix."""
        content_hash = compute_content_hash("string", "key", "value")
        hex_part = content_hash[2:]
        assert all(c in "0123456789abcdef" for c in hex_part), "Hash should contain only hex characters"

    def test_merkle_hash_prefix(self):
        """Verify merkle hash starts with 'm:' prefix."""
        merkle_hash = compute_merkle_hash("object", "root", ["c:abc", "c:def"])
        assert merkle_hash.startswith("m:"), "Merkle hash should start with 'm:'"

    def test_merkle_hash_length(self):
        """Verify merkle hash is 34 chars (m: + 32 hex)."""
        merkle_hash = compute_merkle_hash("object", "root", ["c:abc", "c:def"])
        assert len(merkle_hash) == 34, "Merkle hash should be 34 chars (m: + 32 hex)"

    def test_merkle_hash_hex_characters(self):
        """Verify merkle hash contains only hex characters after prefix."""
        merkle_hash = compute_merkle_hash("object", "root", ["c:abc", "c:def"])
        hex_part = merkle_hash[2:]
        assert all(c in "0123456789abcdef" for c in hex_part), "Hash should contain only hex characters"

    def test_content_hash_distinguishable_from_merkle(self):
        """Verify content and merkle hashes are distinguishable by prefix."""
        content_hash = compute_content_hash("string", "key", "value")
        merkle_hash = compute_merkle_hash("object", "key", ["c:abc"])

        assert content_hash[:2] != merkle_hash[:2], "Content and merkle hashes should have different prefixes"
        assert content_hash.startswith("c:")
        assert merkle_hash.startswith("m:")


class TestEncodeValueForHash:
    """Tests for the encode_value_for_hash function."""

    def test_encode_string_value(self):
        """Test encoding string values."""
        assert encode_value_for_hash("string", "hello", None, None) == "hello"

    def test_encode_string_none_value(self):
        """Test encoding None string value returns empty string."""
        assert encode_value_for_hash("string", None, None, None) == ""

    def test_encode_string_empty_value(self):
        """Test encoding empty string value."""
        assert encode_value_for_hash("string", "", None, None) == ""

    def test_encode_number_integer(self):
        """Test encoding integer number."""
        assert encode_value_for_hash("number", None, 42, None) == "42"

    def test_encode_number_float(self):
        """Test encoding float number."""
        assert encode_value_for_hash("number", None, 3.14, None) == "3.14"

    def test_encode_number_zero(self):
        """Test encoding zero."""
        assert encode_value_for_hash("number", None, 0, None) == "0"

    def test_encode_number_negative(self):
        """Test encoding negative number."""
        assert encode_value_for_hash("number", None, -42, None) == "-42"

    def test_encode_number_none(self):
        """Test encoding None number returns '0'."""
        assert encode_value_for_hash("number", None, None, None) == "0"

    def test_encode_boolean_true(self):
        """Test encoding boolean True."""
        assert encode_value_for_hash("boolean", None, None, True) == "true"

    def test_encode_boolean_false(self):
        """Test encoding boolean False."""
        assert encode_value_for_hash("boolean", None, None, False) == "false"

    def test_encode_boolean_none(self):
        """Test encoding None boolean returns 'false'."""
        assert encode_value_for_hash("boolean", None, None, None) == "false"

    def test_encode_null(self):
        """Test encoding null type."""
        assert encode_value_for_hash("null", None, None, None) == "null"

    def test_encode_unknown_type_with_string(self):
        """Test encoding unknown type falls back to string value."""
        assert encode_value_for_hash("unknown", "fallback", None, None) == "fallback"

    def test_encode_unknown_type_without_string(self):
        """Test encoding unknown type with None string returns empty."""
        assert encode_value_for_hash("unknown", None, None, None) == ""


class TestMerkleHashDeterminism:
    """Tests for merkle hash determinism."""

    def test_merkle_hash_same_regardless_of_child_order(self):
        """Verify merkle hash is deterministic regardless of child order."""
        hash1 = compute_merkle_hash("object", "root", ["c:aaa", "c:bbb", "c:ccc"])
        hash2 = compute_merkle_hash("object", "root", ["c:ccc", "c:aaa", "c:bbb"])

        assert hash1 == hash2, "Merkle hash should be same regardless of child order"

    def test_merkle_hash_deterministic_with_duplicates(self):
        """Verify merkle hash handles duplicate children consistently."""
        hash1 = compute_merkle_hash("array", "items", ["c:aaa", "c:bbb", "c:aaa"])
        hash2 = compute_merkle_hash("array", "items", ["c:aaa", "c:aaa", "c:bbb"])

        assert hash1 == hash2, "Merkle hash should be same with reordered duplicates"

    def test_merkle_hash_different_for_different_children(self):
        """Verify merkle hash differs when children differ."""
        hash1 = compute_merkle_hash("object", "root", ["c:aaa", "c:bbb"])
        hash2 = compute_merkle_hash("object", "root", ["c:aaa", "c:ccc"])

        assert hash1 != hash2, "Merkle hash should differ for different children"

    def test_merkle_hash_different_for_different_keys(self):
        """Verify merkle hash differs when key differs."""
        hash1 = compute_merkle_hash("object", "root1", ["c:aaa", "c:bbb"])
        hash2 = compute_merkle_hash("object", "root2", ["c:aaa", "c:bbb"])

        assert hash1 != hash2, "Merkle hash should differ for different keys"

    def test_merkle_hash_different_for_different_kinds(self):
        """Verify merkle hash differs when kind differs."""
        hash1 = compute_merkle_hash("object", "root", ["c:aaa", "c:bbb"])
        hash2 = compute_merkle_hash("array", "root", ["c:aaa", "c:bbb"])

        assert hash1 != hash2, "Merkle hash should differ for different kinds"

    def test_merkle_hash_empty_children(self):
        """Verify merkle hash works with empty children list."""
        hash1 = compute_merkle_hash("object", "empty", [])
        hash2 = compute_merkle_hash("object", "empty", [])

        assert hash1 == hash2, "Merkle hash should be consistent for empty children"

    def test_merkle_hash_single_child(self):
        """Verify merkle hash works with single child."""
        hash1 = compute_merkle_hash("array", "items", ["c:only"])
        hash2 = compute_merkle_hash("array", "items", ["c:only"])

        assert hash1 == hash2, "Merkle hash should be consistent for single child"


class TestRefCountDiffCalculation:
    """Tests for ref_count change computation logic."""

    def test_ref_count_diff_basic(self):
        """Test the ref_count change computation logic."""
        old_hashes = {"h1", "h2", "h3"}
        new_hashes = {"h2", "h3", "h4"}

        to_decrement = old_hashes - new_hashes
        to_increment = new_hashes - old_hashes
        unchanged = old_hashes & new_hashes

        assert to_decrement == {"h1"}, "Should decrement h1 (only in old)"
        assert to_increment == {"h4"}, "Should increment h4 (only in new)"
        assert unchanged == {"h2", "h3"}, "h2 and h3 should be unchanged"

    def test_ref_count_diff_no_overlap(self):
        """Test ref_count diff when sets have no overlap."""
        old_hashes = {"h1", "h2"}
        new_hashes = {"h3", "h4"}

        to_decrement = old_hashes - new_hashes
        to_increment = new_hashes - old_hashes
        unchanged = old_hashes & new_hashes

        assert to_decrement == {"h1", "h2"}, "Should decrement all old hashes"
        assert to_increment == {"h3", "h4"}, "Should increment all new hashes"
        assert unchanged == set(), "No hashes should be unchanged"

    def test_ref_count_diff_identical_sets(self):
        """Test ref_count diff when sets are identical."""
        old_hashes = {"h1", "h2", "h3"}
        new_hashes = {"h1", "h2", "h3"}

        to_decrement = old_hashes - new_hashes
        to_increment = new_hashes - old_hashes
        unchanged = old_hashes & new_hashes

        assert to_decrement == set(), "No hashes should be decremented"
        assert to_increment == set(), "No hashes should be incremented"
        assert unchanged == {"h1", "h2", "h3"}, "All hashes should be unchanged"

    def test_ref_count_diff_empty_old(self):
        """Test ref_count diff when old set is empty (new document)."""
        old_hashes = set()
        new_hashes = {"h1", "h2", "h3"}

        to_decrement = old_hashes - new_hashes
        to_increment = new_hashes - old_hashes
        unchanged = old_hashes & new_hashes

        assert to_decrement == set(), "No hashes to decrement"
        assert to_increment == {"h1", "h2", "h3"}, "All new hashes should be incremented"
        assert unchanged == set(), "No hashes unchanged"

    def test_ref_count_diff_empty_new(self):
        """Test ref_count diff when new set is empty (document deletion)."""
        old_hashes = {"h1", "h2", "h3"}
        new_hashes = set()

        to_decrement = old_hashes - new_hashes
        to_increment = new_hashes - old_hashes
        unchanged = old_hashes & new_hashes

        assert to_decrement == {"h1", "h2", "h3"}, "All old hashes should be decremented"
        assert to_increment == set(), "No hashes to increment"
        assert unchanged == set(), "No hashes unchanged"

    def test_ref_count_diff_partial_overlap(self):
        """Test ref_count diff with partial overlap."""
        old_hashes = {"a", "b", "c", "d"}
        new_hashes = {"c", "d", "e", "f"}

        to_decrement = old_hashes - new_hashes
        to_increment = new_hashes - old_hashes
        unchanged = old_hashes & new_hashes

        assert to_decrement == {"a", "b"}, "a and b should be decremented"
        assert to_increment == {"e", "f"}, "e and f should be incremented"
        assert unchanged == {"c", "d"}, "c and d should be unchanged"


class TestContentHashConsistency:
    """Tests for content hash consistency."""

    def test_same_input_same_hash(self):
        """Verify same inputs produce same hash."""
        hash1 = compute_content_hash("string", "name", "Alice")
        hash2 = compute_content_hash("string", "name", "Alice")

        assert hash1 == hash2, "Same inputs should produce same hash"

    def test_different_value_different_hash(self):
        """Verify different values produce different hashes."""
        hash1 = compute_content_hash("string", "name", "Alice")
        hash2 = compute_content_hash("string", "name", "Bob")

        assert hash1 != hash2, "Different values should produce different hashes"

    def test_different_key_different_hash(self):
        """Verify different keys produce different hashes."""
        hash1 = compute_content_hash("string", "first_name", "Alice")
        hash2 = compute_content_hash("string", "last_name", "Alice")

        assert hash1 != hash2, "Different keys should produce different hashes"

    def test_different_kind_different_hash(self):
        """Verify different kinds produce different hashes."""
        hash1 = compute_content_hash("string", "val", "123")
        hash2 = compute_content_hash("number", "val", "123")

        assert hash1 != hash2, "Different kinds should produce different hashes"

    def test_unicode_values(self):
        """Verify unicode values are handled correctly."""
        hash1 = compute_content_hash("string", "name", "cafe")
        hash2 = compute_content_hash("string", "name", "cafe")
        hash3 = compute_content_hash("string", "name", "cafe")

        assert hash1 == hash2 == hash3, "Unicode values should hash consistently"

    def test_special_characters(self):
        """Verify special characters are handled correctly."""
        hash1 = compute_content_hash("string", "data", "hello|world|test")
        hash2 = compute_content_hash("string", "data", "hello|world|test")

        assert hash1 == hash2, "Special characters should hash consistently"

    def test_newlines_and_whitespace(self):
        """Verify newlines and whitespace are preserved in hash."""
        hash1 = compute_content_hash("string", "text", "hello\nworld")
        hash2 = compute_content_hash("string", "text", "hello world")

        assert hash1 != hash2, "Newlines should affect hash"
