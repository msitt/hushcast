"""Tests for the changelog fragment collation in .github/scripts/prepare_release.py.

The release workflow is the only writer of CHANGELOG.md, and it runs once, on
release day. These cover the parsing and rendering so a mistake surfaces in CI
instead of half way through a release.
"""

import importlib.util
import subprocess
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


RENOVATE = "renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>"


@pytest.fixture
def repo(release, tmp_path, monkeypatch):
    """A scratch git repo in the release working directory, isolated from the
    user's git config (signing, hooks, default branch) so commits always work."""
    empty_config = tmp_path / "gitconfig"
    empty_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    run_git("init", "-q")
    run_git("config", "user.name", "Maintainer")
    run_git("config", "user.email", "maintainer@example.com")
    return release


def run_git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def commit(author: str | None = None) -> None:
    extra = ["--author", author] if author else []
    run_git("commit", "-q", "--allow-empty", "-m", "change", *extra)


def test_renovate_commit_since_last_tag_is_a_dependency_update(repo):
    commit()
    run_git("tag", "v1.0.0")
    commit()
    commit(RENOVATE)

    assert repo.has_dependency_updates()


def test_renovate_commit_before_last_tag_is_already_released(repo):
    commit(RENOVATE)
    run_git("tag", "v1.0.0")
    commit()

    assert not repo.has_dependency_updates()


def test_first_release_looks_at_all_history(repo):
    commit(RENOVATE)
    commit()

    assert repo.has_dependency_updates()


def test_shallow_checkout_is_rejected(repo, tmp_path, monkeypatch, capsys):
    commit()
    commit()
    clone = tmp_path / "clone"
    run_git("clone", "-q", "--depth", "1", f"file://{tmp_path.as_posix()}", str(clone))
    monkeypatch.chdir(clone)

    with pytest.raises(SystemExit):
        repo.has_dependency_updates()

    assert "fetch-depth: 0" in capsys.readouterr().err
