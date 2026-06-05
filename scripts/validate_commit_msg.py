from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_PREFIXES = {
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "style",
    "test",
}
COMMIT_RE = re.compile(rf"^({'|'.join(sorted(ALLOWED_PREFIXES))})(\([a-z0-9][a-z0-9-]*\))?: [^\s].{{0,99}}$")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_commit_msg.py <commit-msg-file>", file=sys.stderr)
        return 2

    message = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[0].strip()
    if message.startswith(("Merge ", "Revert ", "fixup!", "squash!")):
        return 0
    if COMMIT_RE.match(message):
        return 0

    print(
        f"Invalid commit message. Use: type(scope): summary, where type is one of {', '.join(sorted(ALLOWED_PREFIXES))}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
