# Implementation plan: word_stats `--top N`

`word_stats.py` is a tiny word-frequency CLI. It reads a text file and prints
`word: count` for every word, sorted alphabetically. This plan adds the
ability to show only the most frequent N words.

## Phase 1 — `most_common(text, n)` helper (`cp1_most_common`)

Add a `most_common(text: str, n: int) -> list[tuple[str, int]]` function to
`src/word_stats.py`. It should reuse the existing `word_counts()` helper and
return the `n` most frequent words as `(word, count)` pairs, ordered by count
descending, ties broken alphabetically. Add unit tests.

**Exit criteria**
- `most_common("the cat sat on the mat the cat ran", 2) == [("the", 3), ("cat", 2)]`
- Ties are broken alphabetically (e.g. `most_common("b a b a", 2) == [("a", 2), ("b", 2)]`)
- `make test` passes

## Phase 2 — `--top N` CLI flag (`cp2_cli_flag`)

Add an optional `--top N` argument to `word_stats.py`'s CLI. When passed,
print only the top `N` words (via `most_common`) instead of the full
alphabetical listing, most-frequent first, same `word: count` format.
Behavior with no `--top` flag must be unchanged.

**Exit criteria**
- `python3 src/word_stats.py <file> --top 2` prints exactly 2 lines,
  most-frequent word first
- Omitting `--top` behaves exactly as before (full alphabetical listing)
- `make test` passes
