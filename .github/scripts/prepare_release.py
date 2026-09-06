"""Prepare a release: bump versions and build the changelog.

Usage:
  prepare_release.py X.Y.Z /path/to/release_notes.md   prepare a release
  prepare_release.py --check                           validate fragments only

Release notes are written as one file per change in changelog.d/ (see
changelog.d/README.md). Nothing is ever hand-edited in CHANGELOG.md, which
keeps concurrent work and releases from fighting over the same lines.

Release steps:
  - requires at least one well-formed fragment in changelog.d/
  - bumps __version__ in backend/hushcast/__init__.py
  - bumps "version" in frontend/package.json and frontend/package-lock.json
  - renders the fragments into a new [X.Y.Z] - <today> section at the top of
    CHANGELOG.md, and deletes the fragment files
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
FRAGMENT_DIR = Path("changelog.d")

# Keep a Changelog's section names, in the order they should be rendered.
CATEGORIES = ["added", "changed", "deprecated", "removed", "fixed", "security"]


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_fragments() -> dict[str, list[tuple[Path, str]]]:
    """Read and validate changelog.d/, grouped by category.

    A fragment is named <category>-<slug>.md and holds the markdown bullet(s)
    for one change, leading "- " included. Fragments sort by filename within
    their category, so rendering is deterministic.
    """
    if not FRAGMENT_DIR.is_dir():
        fail(f"{FRAGMENT_DIR}/ does not exist")

    grouped: dict[str, list[tuple[Path, str]]] = {c: [] for c in CATEGORIES}
    for path in sorted(FRAGMENT_DIR.glob("*.md")):
        if path.name == "README.md":
            continue

        category = path.stem.split("-", 1)[0].lower()
        if category not in CATEGORIES:
            fail(
                f"{path}: unknown category {category!r}. Name fragments "
                f"<category>-<slug>.md, where category is one of: "
                f"{', '.join(CATEGORIES)}"
            )

        body = path.read_text(encoding="utf-8").strip()
        if not body:
            fail(f"{path} is empty")
        if not body.startswith("- "):
            fail(
                f"{path} must start with a markdown bullet ('- '), so it can be "
                f"dropped into a changelog section as-is"
            )

        grouped[category].append((path, body))

    return grouped


def render_section(grouped: dict[str, list[tuple[Path, str]]]) -> str:
    """Render the fragments as a changelog section body (no version heading)."""
    blocks = []
    for category in CATEGORIES:
        entries = grouped[category]
        if not entries:
            continue
        bullets = "\n".join(body for _, body in entries)
        blocks.append(f"### {category.capitalize()}\n\n{bullets}")
    return "\n\n".join(blocks)


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        grouped = load_fragments()
        count = sum(len(v) for v in grouped.values())
        print(f"changelog.d: {count} fragment(s), all well-formed")
        return

    if len(sys.argv) != 3:
        fail(
            "usage: prepare_release.py X.Y.Z /path/to/release_notes.md\n"
            "       prepare_release.py --check"
        )
    version, notes_path = sys.argv[1], Path(sys.argv[2])

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fail(f"version {version!r} doesn't look like semver (X.Y.Z)")

    grouped = load_fragments()
    body = render_section(grouped)
    if not body:
        fail(
            f"no release notes in {FRAGMENT_DIR}/. Add a fragment for each "
            f"user-visible change before releasing"
        )

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
    if f"## [{version}]" in changelog:
        fail(f"{CHANGELOG} already has a [{version}] section")
    anchor = re.search(r"(?m)^## \[", changelog)
    if not anchor:
        fail(f"{CHANGELOG} has no existing release section to insert above")

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
    section = f"## [{version}] - {today}\n\n{body}\n\n"
    CHANGELOG.write_text(
        changelog[: anchor.start()] + section + changelog[anchor.start() :],
        encoding="utf-8",
        newline="",
    )

    for entries in grouped.values():
        for path, _ in entries:
            path.unlink()

    notes_path.write_text(body + "\n", encoding="utf-8")
    print(f"prepared release {version}")


if __name__ == "__main__":
    main()
