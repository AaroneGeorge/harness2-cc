Write user-facing release notes for version 2.4.0 from `git-log.txt`, which has one commit per line in the form `<hash> <type>(<scope>): <subject>`. A `!` after the scope marks a breaking change.

Write `output/RELEASE_NOTES.md` with:

1. A title line `# 2.4.0`.
2. These sections, in this order, each a `##` heading followed by bullets: `## Breaking Changes`, `## Features`, `## Bug Fixes`, `## Documentation`.
3. One bullet per commit, starting with the scope in bold, for example `- **api**: Add cursor-based pagination to GET /projects`.

Rules:

- Breaking changes go only under Breaking Changes, and the bullet must tell the reader what to change.
- `feat` goes under Features, `fix` under Bug Fixes, `docs` under Documentation.
- Leave out every `chore` and `refactor` commit.
- Do not include commit hashes or the raw `type(scope):` prefixes.
