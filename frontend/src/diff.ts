// Word-level diff for the version-compare view (Phase 10). Client-side and
// dependency-free on purpose: note sections are a few sentences of prose,
// far too small to justify a diff library, and both versions being
// compared are already fetched through the existing
// GET /api/encounters/{id}/versions/{n} endpoint — no new backend surface.
//
// Classic O(n*m) LCS table over WORDS (not characters, which would produce
// noisy sub-word diffs on typo-scale edits — irrelevant for clinical
// prose). Splitting on a capturing whitespace regex keeps every space and
// newline as its own token, so unchanged whitespace (including a
// multi-line PLAN's own formatting) is preserved exactly rather than
// normalized away.

export interface DiffToken {
  text: string;
  type: "same" | "added" | "removed";
}

export function wordDiff(oldText: string, newText: string): DiffToken[] {
  const a = oldText.split(/(\s+)/).filter((t) => t !== "");
  const b = newText.split(/(\s+)/).filter((t) => t !== "");
  const n = a.length;
  const m = b.length;

  // dp[i][j] = length of the LCS of a[i:] and b[j:].
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const tokens: DiffToken[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      tokens.push({ text: a[i], type: "same" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      tokens.push({ text: a[i], type: "removed" });
      i++;
    } else {
      tokens.push({ text: b[j], type: "added" });
      j++;
    }
  }
  while (i < n) {
    tokens.push({ text: a[i], type: "removed" });
    i++;
  }
  while (j < m) {
    tokens.push({ text: b[j], type: "added" });
    j++;
  }
  return tokens;
}
