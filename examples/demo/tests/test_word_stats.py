"""Tests for word_stats.py.

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
(or `make test` from examples/demo/)

TestMostCommon and TestTopFlag describe the feature the demo checkpoints add.
They fail against the starting scaffold on purpose -- that's the point: it's
real, verifiable work for the developer agent to do, and real criteria for
the reviewer agent to check.
"""
import subprocess
import sys
import unittest
from collections import Counter

from word_stats import format_counts, word_counts


class TestWordCounts(unittest.TestCase):
    def test_counts_basic(self):
        self.assertEqual(
            word_counts("the cat sat on the mat"),
            Counter({"the": 2, "cat": 1, "sat": 1, "on": 1, "mat": 1}),
        )

    def test_case_insensitive(self):
        self.assertEqual(word_counts("Cat cat CAT"), Counter({"cat": 3}))


class TestFormatCounts(unittest.TestCase):
    def test_format_sorted_alphabetically(self):
        self.assertEqual(format_counts(sorted({"cat": 1, "the": 2}.items())), "cat: 1\nthe: 2")


# --- cp1_most_common: not implemented yet in the starting scaffold ---
class TestMostCommon(unittest.TestCase):
    def test_most_common_orders_by_frequency_desc(self):
        from word_stats import most_common  # exists only after cp1_most_common

        text = "the cat sat on the mat the cat ran"
        self.assertEqual(most_common(text, 2), [("the", 3), ("cat", 2)])

    def test_most_common_breaks_ties_alphabetically(self):
        from word_stats import most_common

        self.assertEqual(most_common("b a b a", 2), [("a", 2), ("b", 2)])


# --- cp2_cli_flag: not implemented yet in the starting scaffold ---
class TestTopFlag(unittest.TestCase):
    def _run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "src/word_stats.py", *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_top_flag_prints_only_n_most_frequent(self):
        with open("sample.txt", "w", encoding="utf-8") as f:
            f.write("the cat sat on the mat the cat ran")
        result = self._run_cli("sample.txt", "--top", "2")
        self.assertEqual(result.stdout.strip().splitlines(), ["the: 3", "cat: 2"])

    def test_no_flag_behavior_is_unchanged(self):
        with open("sample.txt", "w", encoding="utf-8") as f:
            f.write("the cat sat")
        result = self._run_cli("sample.txt")
        self.assertEqual(result.stdout.strip().splitlines(), ["cat: 1", "sat: 1", "the: 1"])


if __name__ == "__main__":
    unittest.main()
