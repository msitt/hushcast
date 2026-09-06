"""Tests for the changelog fragment collation in .github/scripts/prepare_release.py.

The release workflow is the only writer of CHANGELOG.md, and it runs once, on
release day. These cover the parsing and rendering so a mistake surfaces in CI
instead of half way through a release.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "prepare_release.py"


def load_script():
    spec = importlib.util.spec_from_file_location("prepare_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release(tmp_path, monkeypatch):
    """The script module, with the working directory pointed at a scratch repo."""
    (tmp_path / "changelog.d").mkdir()
    monkeypatch.chdir(tmp_path)
    return load_script()


def write(name: str, body: str) -> None:
    Path("changelog.d", name).write_text(body, encoding="utf-8")


def test_sections_render_in_keep_a_changelog_order(release):
    write("fixed-crash.md", "- Fixed a crash.")
    write("added-search.md", "- Added search.")
    write("removed-knob.md", "- Removed a knob.")

    rendered = release.render_section(release.load_fragments())

    # Categories with no fragments are left out entirely.
    assert rendered == (
        "### Added\n\n- Added search.\n\n"
        "### Removed\n\n- Removed a knob.\n\n"
        "### Fixed\n\n- Fixed a crash."
    )


def test_fragments_sort_by_filename_within_a_category(release):
    write("added-zebra.md", "- Zebra.")
    write("added-apple.md", "- Apple.")

    rendered = release.render_section(release.load_fragments())

    assert rendered == "### Added\n\n- Apple.\n- Zebra."


def test_multi_bullet_fragment_is_kept_verbatim(release):
    write("changed-two.md", "- First bullet.\n- Second bullet.")

    assert release.render_section(release.load_fragments()) == (
        "### Changed\n\n- First bullet.\n- Second bullet."
    )


def test_readme_is_not_a_fragment(release):
    write("README.md", "How to write fragments.")

    assert release.render_section(release.load_fragments()) == ""


def test_unknown_category_is_rejected(release, capsys):
    write("feature-oops.md", "- Nope.")

    with pytest.raises(SystemExit) as exc:
        release.load_fragments()

    assert exc.value.code == 1
    assert "unknown category 'feature'" in capsys.readouterr().err


def test_fragment_without_a_bullet_is_rejected(release, capsys):
    write("fixed-plain.md", "Fixed a thing.")

    with pytest.raises(SystemExit) as exc:
        release.load_fragments()

    assert exc.value.code == 1
    assert "must start with a markdown bullet" in capsys.readouterr().err


def test_empty_fragment_is_rejected(release, capsys):
    write("fixed-empty.md", "\n")

    with pytest.raises(SystemExit) as exc:
        release.load_fragments()

    assert exc.value.code == 1
    assert "is empty" in capsys.readouterr().err


def test_no_fragments_renders_nothing(release):
    """The normal state right after a release. --check has to stay green here."""
    assert release.render_section(release.load_fragments()) == ""
