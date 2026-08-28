"""Windows-shaped gitdir-pointer translation for WSL2.

A Windows-created linked git worktree has a `.git` *file* whose body is
`gitdir: I:/apps/.../.git/worktrees/<name>`. Git running under WSL2 reads
that literally and cannot resolve it. Covers `pontonier.core.wslpath`:

  - `normalize_wsl_drive_path(value)` -- pure string translation of a
    Windows drive-letter path (`X:/foo` / `X:\\foo`) to its WSL2 mount form
    (`/mnt/x/foo`); everything else is returned unchanged.
  - `linked_worktree_gitdir(cwd)` -- parses `<cwd>/.git` as a
    linked-worktree `gitdir:` pointer file and returns the translated
    absolute gitdir when the pointer is Windows-shaped, else `None`.
  - `git_dir_override(cwd)` -- the `_base_git_env` chokepoint: `{}` in
    every ordinary case (the containment property); walks up from `cwd`
    to the first directory carrying a `.git` marker (a depth/filesystem-
    boundary ceiling bounds the walk) and returns
    `{"GIT_DIR": ..., "GIT_WORK_TREE": ...}` only when that marker is a
    Windows-shaped linked-worktree pointer.

Ported from `codex-in-claude`'s `_core/wslpath.py` fix (this fork's WSL2
port, see `codex-in-claude#13`) -- every case is covered, plus the
walk-up/ceiling behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pontonier.core import wslpath


def _write_gitdir_file(git_file: Path, body: str) -> None:
    """Write a `.git` file's body, exactly as git itself would.

    Args:
        git_file: The `.git` file path to create.
        body: The raw text content of the file (the `gitdir: ...` line, or
            any malformed/empty body a test wants to exercise).
    """
    git_file.write_text(body)


# --- normalize_wsl_drive_path: pure string translation ------------------------


def test_normalize_forward_slash_drive_path():
    assert wslpath.normalize_wsl_drive_path("I:/apps/x/.git") == "/mnt/i/apps/x/.git"


def test_normalize_backslash_drive_path():
    assert wslpath.normalize_wsl_drive_path("I:\\apps\\x\\.git") == "/mnt/i/apps/x/.git"


def test_normalize_lowercase_drive_letter_stays_lowercase():
    assert wslpath.normalize_wsl_drive_path("i:/apps/x") == "/mnt/i/apps/x"


def test_normalize_uppercase_drive_letter_is_lowercased():
    assert wslpath.normalize_wsl_drive_path("I:/apps/x") == "/mnt/i/apps/x"


def test_normalize_posix_path_returned_unchanged():
    assert wslpath.normalize_wsl_drive_path("/mnt/i/apps/x") == "/mnt/i/apps/x"


def test_normalize_relative_path_returned_unchanged():
    value = "../../.git/worktrees/n"
    assert wslpath.normalize_wsl_drive_path(value) == value


def test_normalize_unc_path_returned_unchanged():
    # UNC paths are explicitly out of scope -- must not half-translate.
    value = "\\\\server\\share\\repo"
    assert wslpath.normalize_wsl_drive_path(value) == value


def test_normalize_empty_string_returned_unchanged():
    assert wslpath.normalize_wsl_drive_path("") == ""


def test_normalize_never_raises_on_malformed_input():
    # A single character, a bare colon, and other malformed near-misses must
    # fall through to "everything else returned unchanged" rather than raise.
    for value in (":", "I:", "I", "I:/", ":/foo", "1:/foo"):
        try:
            result = wslpath.normalize_wsl_drive_path(value)
        except Exception as exc:  # the assertion IS "never raises" -- catch broadly
            pytest.fail(f"normalize_wsl_drive_path({value!r}) raised {exc!r}")
        assert isinstance(result, str)


# --- linked_worktree_gitdir: parses <cwd>/.git as a gitdir pointer -----------


def test_dot_git_is_a_directory_returns_none(tmp_path):
    (tmp_path / ".git").mkdir()
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_dot_git_missing_entirely_returns_none(tmp_path):
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_posix_gitdir_pointer_returns_none(tmp_path):
    # A native WSL linked worktree's own pointer must be left untouched.
    _write_gitdir_file(tmp_path / ".git", "gitdir: /home/u/repo/.git/worktrees/wt\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_windows_forward_slash_drive_path_translated(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir: I:/apps/x/.git/worktrees/n\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) == "/mnt/i/apps/x/.git/worktrees/n"


def test_windows_backslash_drive_path_translated(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir: I:\\apps\\x\\.git\\worktrees\\n\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) == "/mnt/i/apps/x/.git/worktrees/n"


@pytest.mark.parametrize("drive_letter", ["i", "I"])
def test_drive_letter_case_both_translate_to_lowercase_mnt(tmp_path, drive_letter):
    _write_gitdir_file(tmp_path / ".git", f"gitdir: {drive_letter}:/apps/x/.git/worktrees/n\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) == "/mnt/i/apps/x/.git/worktrees/n"


def test_relative_forward_slash_pointer_not_mistaken_for_drive_path(tmp_path):
    # git writes relative `gitdir:` values under `--relative-paths`; the value must be
    # resolved against the `.git` file's own directory (which, under WSL, is already a
    # plain filesystem path) and must never be misdetected as an absolute Windows drive
    # path -- it starts with "." or "..", never "X:".
    _write_gitdir_file(tmp_path / ".git", "gitdir: ../../real/.git/worktrees/wt\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_relative_backslash_pointer_not_mistaken_for_drive_path(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir: ..\\..\\real\\.git\\worktrees\\wt\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_empty_gitdir_file_returns_none(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_gitdir_file_without_gitdir_key_returns_none(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "not a gitdir pointer at all\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


def test_gitdir_file_with_empty_value_never_raises(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir:\n")
    try:
        result = wslpath.linked_worktree_gitdir(str(tmp_path))
    except Exception as exc:  # the assertion IS "never raises" -- catch broadly
        pytest.fail(f"linked_worktree_gitdir must never raise, raised {exc!r}")
    assert result is None


def test_gitdir_file_with_only_whitespace_never_raises(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "   \n\n")
    try:
        result = wslpath.linked_worktree_gitdir(str(tmp_path))
    except Exception as exc:  # the assertion IS "never raises" -- catch broadly
        pytest.fail(f"linked_worktree_gitdir must never raise, raised {exc!r}")
    assert result is None


def test_unc_path_gitdir_pointer_returns_none(tmp_path):
    # UNC values are out of scope -- must not half-translate.
    _write_gitdir_file(tmp_path / ".git", "gitdir: \\\\server\\share\\repo\\.git\\worktrees\\n\n")
    assert wslpath.linked_worktree_gitdir(str(tmp_path)) is None


# --- git_dir_override: the _base_git_env chokepoint ---------------------------


def test_git_dir_override_empty_for_ordinary_checkout(tmp_path):
    (tmp_path / ".git").mkdir()
    assert wslpath.git_dir_override(str(tmp_path)) == {}


def test_git_dir_override_empty_when_dot_git_missing(tmp_path):
    assert wslpath.git_dir_override(str(tmp_path)) == {}


def test_git_dir_override_empty_for_posix_linked_worktree(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir: /home/u/repo/.git/worktrees/wt\n")
    assert wslpath.git_dir_override(str(tmp_path)) == {}


def test_git_dir_override_translates_windows_shaped_pointer_at_cwd(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir: I:/apps/x/.git/worktrees/n\n")
    override = wslpath.git_dir_override(str(tmp_path))
    assert override == {
        "GIT_DIR": "/mnt/i/apps/x/.git/worktrees/n",
        "GIT_WORK_TREE": str(tmp_path),
    }


def test_git_dir_override_malformed_body_returns_empty_never_a_partial_dict(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "garbage\n")
    assert wslpath.git_dir_override(str(tmp_path)) == {}


# --- git_dir_override: walk-up for a sub-directory cwd -------------------------
#
# Walk up from `cwd` to the first directory containing a `.git` file/dir (with
# a depth/filesystem-boundary ceiling), setting GIT_WORK_TREE to *that*
# directory -- never to `cwd` itself, which would be actively wrong (git stops
# discovery once GIT_DIR is set).


def test_git_dir_override_walks_up_to_ordinary_git_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    assert wslpath.git_dir_override(str(sub)) == {}


def test_git_dir_override_walks_up_to_windows_shaped_pointer(tmp_path):
    _write_gitdir_file(tmp_path / ".git", "gitdir: I:/apps/x/.git/worktrees/n\n")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    override = wslpath.git_dir_override(str(sub))
    assert override == {
        "GIT_DIR": "/mnt/i/apps/x/.git/worktrees/n",
        "GIT_WORK_TREE": str(tmp_path),  # the discovered root, NOT `sub`
    }


def test_git_dir_override_walk_up_stops_at_the_first_marker_found(tmp_path):
    # An ordinary, closer `.git` directory must shadow a Windows-shaped pointer
    # further up the tree -- the walk stops at the FIRST marker, not the topmost.
    _write_gitdir_file(tmp_path / ".git", "gitdir: I:/apps/x/.git/worktrees/n\n")
    nested_repo = tmp_path / "vendored"
    nested_repo.mkdir()
    (nested_repo / ".git").mkdir()
    sub = nested_repo / "src"
    sub.mkdir()
    assert wslpath.git_dir_override(str(sub)) == {}


def test_git_dir_override_returns_absolute_work_tree_for_relative_cwd(tmp_path, monkeypatch):
    # When `cwd` is relative, the walk-up must still re-anchor it to an absolute
    # path -- `GIT_WORK_TREE` must always be absolute, however relative the
    # input `cwd` was, or git receives a relative path after changing to `cwd`
    # and can fail.
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _write_gitdir_file(repo_dir / ".git", "gitdir: I:/apps/x/.git/worktrees/n\n")
    nested = repo_dir / "src" / "pkg"
    nested.mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    override = wslpath.git_dir_override("repo/src/pkg")

    assert Path(override["GIT_WORK_TREE"]).is_absolute()
    assert Path(override["GIT_WORK_TREE"]) == repo_dir


def test_git_dir_override_returns_empty_when_no_dot_git_within_ceiling(tmp_path):
    # A moderately deep chain with no `.git` marker anywhere must terminate and
    # report "no override" rather than hang or raise -- the walk-up is bounded
    # (a depth/filesystem-boundary ceiling), never unbounded. The exact ceiling
    # value is an implementation choice; this asserts only the observable
    # contract (terminates, reports "not found") common to any reasonable bound.
    deep = tmp_path
    for i in range(25):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    assert wslpath.git_dir_override(str(deep)) == {}
