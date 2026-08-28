"""Workspace resolution precedence and validation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pontonier.core import workspace


def test_resolve_explicit_param(tmp_path):
    res = workspace.resolve_workspace(str(tmp_path), [], "/server/cwd")
    assert res.source == "param"
    assert res.path == str(tmp_path.resolve())
    assert res.error_code is None


def test_resolve_explicit_must_be_absolute(tmp_path):
    res = workspace.resolve_workspace("relative/path", [], "/server/cwd")
    assert res.error_code == "invalid_workspace_root"


def test_resolve_explicit_not_a_dir(tmp_path):
    missing = tmp_path / "nope"
    res = workspace.resolve_workspace(str(missing), [], "/server/cwd")
    assert res.error_code == "invalid_workspace_root"


def test_resolve_explicit_outside_roots(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    res = workspace.resolve_workspace(str(other), [str(root)], "/server/cwd")
    assert res.error_code == "workspace_outside_roots"


def test_resolve_explicit_inside_roots(tmp_path):
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    res = workspace.resolve_workspace(str(sub), [str(root)], "/server/cwd")
    assert res.source == "param"
    assert res.error_code is None


def test_resolve_from_roots(tmp_path):
    res = workspace.resolve_workspace(None, [str(tmp_path)], "/server/cwd")
    assert res.source == "roots"
    assert res.path == str(tmp_path.resolve())


def test_resolve_from_cwd_fallback(tmp_path):
    res = workspace.resolve_workspace(None, [], str(tmp_path))
    assert res.source == "cwd"
    assert res.path == str(tmp_path.resolve())


# --- normalize_wsl_drive_path -------------------------------------------------
#
# WSL2-hosted server + native-Windows MCP client: the client advertises its
# workspace roots as `file://` URIs like `file:///I:/ai/claude/claude-configs`.
# Decoding that URI yields the literal POSIX-shaped string
# `/I:/ai/claude/claude-configs` -- a string that looks absolute but names no
# real path on the WSL2 server's own filesystem (the real path is
# `/mnt/i/ai/claude/claude-configs`). `normalize_wsl_drive_path` rewrites the
# former into the latter; every other shape of input must pass through
# unchanged, since the same helper is called unconditionally on decoded roots
# and on any explicit `workspace_root` a caller supplies, regardless of
# platform or client. Referenced via `workspace.normalize_wsl_drive_path`
# (rather than imported at module scope) so a missing attribute fails only
# these tests, not collection of the whole file.
#
# Ported from `codex-in-claude`'s WSL2 fix (this fork's WSL2 port, see
# `codex-in-claude#13`). NOTE: this is a *different* function from
# `pontonier.core.wslpath.normalize_wsl_drive_path` -- that one requires no
# leading slash and translates a raw `gitdir:` pointer body; this one
# requires a leading slash and translates a decoded `file:///I:/...` MCP
# root URI. They are not duplicates and must not be unified.


def test_normalize_wsl_drive_path_translates_nested_subpath():
    result = workspace.normalize_wsl_drive_path("/I:/ai/claude/claude-configs")
    assert result == "/mnt/i/ai/claude/claude-configs"


def test_normalize_wsl_drive_path_translates_bare_drive_root_no_trailing_slash():
    result = workspace.normalize_wsl_drive_path("/C:")
    assert result == "/mnt/c"


def test_normalize_wsl_drive_path_lowercases_the_drive_letter_in_output():
    result = workspace.normalize_wsl_drive_path("/c:/Users/chris")
    assert result == "/mnt/c/Users/chris"


def test_normalize_wsl_drive_path_uppercase_letter_also_lowercased():
    result = workspace.normalize_wsl_drive_path("/Z:/foo/bar")
    assert result == "/mnt/z/foo/bar"


def test_normalize_wsl_drive_path_leaves_already_wsl_style_path_unchanged():
    already_wsl = "/mnt/i/ai/claude/claude-configs"
    assert workspace.normalize_wsl_drive_path(already_wsl) == already_wsl


def test_normalize_wsl_drive_path_leaves_unrelated_posix_path_unchanged():
    posix_path = "/home/chris/repo"
    assert workspace.normalize_wsl_drive_path(posix_path) == posix_path


def test_normalize_wsl_drive_path_does_not_mistranslate_multi_char_pseudo_drive():
    # "ab" is not a single drive letter -- must not be mistaken for one.
    unchanged = "/ab:/foo"
    assert workspace.normalize_wsl_drive_path(unchanged) == unchanged


def test_normalize_wsl_drive_path_does_not_mistranslate_non_alphabetic_segment():
    # "1" is not a letter -- must not be mistaken for a drive letter.
    unchanged = "/1:/foo"
    assert workspace.normalize_wsl_drive_path(unchanged) == unchanged


def test_normalize_wsl_drive_path_leaves_non_absolute_drive_shape_unchanged():
    # No leading slash: this is not the decoded-file://-URI shape the helper
    # targets (a relative-looking string), so it must not be rewritten even
    # though "I:/foo" superficially resembles a drive path.
    unchanged = "I:/foo"
    assert workspace.normalize_wsl_drive_path(unchanged) == unchanged


def test_normalize_wsl_drive_path_leaves_empty_string_unchanged():
    assert workspace.normalize_wsl_drive_path("") == ""


# --- resolve_workspace() end-to-end with the new normalization ---------------


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "normalize_wsl_drive_path's /mnt/<letter> output is only ever "
        "absolute under PosixPath. WindowsPath.is_absolute() returns False "
        "for any leading-slash string that has no drive component (verified "
        "empirically: Path('/mnt/i/x').is_absolute() is False on Windows), "
        "so this scenario cannot resolve on native Windows regardless of "
        "whether the fix is applied -- it only makes sense on the POSIX "
        "filesystem semantics of the real WSL2/Linux deployment target."
    ),
)
def test_resolve_workspace_normalizes_drive_letter_explicit_root(monkeypatch):
    """Proves the reported WSL2 bug is fixed at the `resolve_workspace()`
    integration point (not just in the standalone helper): an explicit
    `workspace_root` that still carries the raw decoded-drive-letter shape
    (e.g. a caller reused the client's advertised root string verbatim,
    before that root has been translated) must resolve successfully once
    `normalize_wsl_drive_path` is applied to `explicit` ahead of the
    `is_absolute()` / `is_dir()` / roots-membership checks -- matching this
    project's `roots` docstring contract that `roots` passed in are already
    absolute, already-translated filesystem paths.

    The real WSL mount-point directory this test targets
    (/mnt/i/ai/claude/claude-configs) cannot exist on any CI runner (there is
    no real WSL mount outside an actual WSL2 host), so `Path.is_dir` is
    monkeypatched to treat exactly that one expected, normalized path as
    present -- every other path still hits the real filesystem check
    unchanged. Only `explicit`'s resolved path is ever is_dir()-checked by
    `resolve_workspace` (`roots` are only ever compared as strings via
    `relative_to`), so this is the only filesystem call that needs faking.
    """
    normalized_explicit = "/mnt/i/ai/claude/claude-configs"
    already_translated_root = "/mnt/i/ai/claude"
    real_is_dir = Path.is_dir

    def fake_is_dir(self: Path) -> bool:
        return str(self) == normalized_explicit or real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    res = workspace.resolve_workspace(
        "/I:/ai/claude/claude-configs",
        [already_translated_root],
        "/server/cwd",
    )

    assert res.error_code is None
    assert res.error_detail is None
    assert res.path == normalized_explicit
    assert res.source == "param"
