"""word_stats: a tiny word-frequency CLI.

Reads a text file and prints how many times each word appears, one
"word: count" line per word, sorted alphabetically.

This is the deliberately-incomplete "before" state for the agent-loop meetup
demo: it has no way to show only the most frequent words. See
../docs/implementation_plan.md and ../plan_checkpoints.json for the two
checkpoints that add that feature.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

_WORD_RE = re.compile(r"[a-zA-Z']+")


def word_counts(text: str) -> Counter:
    """Return a Counter of lowercase words found in `text`."""
    return Counter(word.lower() for word in _WORD_RE.findall(text))


def format_counts(pairs) -> str:
    """Format an iterable of (word, count) pairs as one 'word: count' per line."""
    return "\n".join(f"{word}: {count}" for word, count in pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print word frequency counts for a text file.")
    parser.add_argument("path", help="path to a text file")
    args = parser.parse_args(argv)

    with open(args.path, encoding="utf-8") as f:
        text = f.read()

    counts = word_counts(text)
    print(format_counts(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
