"""Prepare a release: bump versions and roll the changelog.

Usage: python prepare_release.py X.Y.Z /path/to/release_notes.md

Steps:
  - requires a non-empty [Unreleased] section in CHANGELOG.md
  - bumps __version__ in backend/hushcast/__init__.py
  - bumps "version" in frontend/package.json and frontend/package-lock.json
  - renames [Unreleased] to [X.Y.Z] - <today> and opens a fresh
    empty [Unreleased] section above it
  - writes the released section's body to the release-notes path
    (used as the GitHub Release body)

Exits non-zero, with nothing modified, on any validation failure.
"""

import datetime
import re
import sys
from pathlib import Path

INIT_FILE = Path("backend/hushcast/__init__.py")
PACKAGE_JSON = Path("frontend/package.json")
PACKAGE_LOCK_JSON = Path("frontend/package-lock.json")
CHANGELOG = Path("CHANGELOG.md")


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: prepare_release.py X.Y.Z /path/to/release_notes.md")
    version, notes_path = sys.argv[1], Path(sys.argv[2])

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"version {version!r} doesn't look like semver (X.Y.Z)")

    init_content = INIT_FILE.read_text(encoding="utf-8")
    if not re.search(r'__version__\s*=\s*"[^"]+"', init_content):
        fail(f"could not find __version__ in {INIT_FILE}")

    lock_content = PACKAGE_LOCK_JSON.read_text(encoding="utf-8")
    lock_pattern = r'("name":\s*"hushcast-frontend",\s*\n\s*"version":\s*)"[^"]+"'
    if len(re.findall(lock_pattern, lock_content)) != 2:
        fail(
            f"expected exactly two hushcast-frontend name/version pairs in "
            f"{PACKAGE_LOCK_JSON}, found {len(re.findall(lock_pattern, lock_content))}"
        )

    changelog = CHANGELOG.read_text(encoding="utf-8")
    if "## [Unreleased]" not in changelog:
        fail(f"{CHANGELOG} has no [Unreleased] section")
    m = re.search(
        r"(?ms)^## \[Unreleased\][ \t]*\r?\n(?P<body>.*?)(?=^## \[|\Z)", changelog
    )
    body = m.group("body").strip() if m else ""
    if not body:
        fail(f"{CHANGELOG} has no entries under [Unreleased]. Add release notes first")
    if f"## [{version}]" in changelog:
        fail(f"{CHANGELOG} already has a [{version}] section")

    # All validation passed, now modify files.
    INIT_FILE.write_text(
        re.sub(
            r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', init_content
        ),
        encoding="utf-8",
        newline="",
    )

    pkg = PACKAGE_JSON.read_text(encoding="utf-8")
    PACKAGE_JSON.write_text(
        re.sub(r'"version"\s*:\s*"[^"]+"', f'"version": "{version}"', pkg, count=1),
        encoding="utf-8",
        newline="",
    )

    PACKAGE_LOCK_JSON.write_text(
        re.sub(lock_pattern, rf'\g<1>"{version}"', lock_content),
        encoding="utf-8",
        newline="",
    )

    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    CHANGELOG.write_text(
        changelog.replace(
            "## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - {today}", 1
        ),
        encoding="utf-8",
        newline="",
    )

    notes_path.write_text(body + "\n", encoding="utf-8")
    print(f"prepared release {version}")


if __name__ == "__main__":
    main()
