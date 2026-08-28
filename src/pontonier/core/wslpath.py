"""Translate Windows-shaped linked-worktree pointers for WSL2 git calls.

A Windows-created linked git worktree has a ``.git`` *file* whose body is
``gitdir: I:/apps/.../.git/worktrees/<name>``. Git running under WSL2 reads
that literally and cannot resolve it -- these helpers detect the
Windows-drive shape and translate it to the WSL2 mount form
(``/mnt/<drive>/...``) so the same worktree is usable from either side.
CLI-agnostic; contains no bridge-specific identifier."""

from __future__ import annotations

import re
from pathlib import Path

_WINDOWS_DRIVE_PATH_RE = re.compile(r"^(?P<drive>[A-Za-z]):[/\\\\](?P<rest>.*)$")
_MAX_PARENT_LEVELS = 64


def normalize_wsl_drive_path(value: str) -> str:
    """Translate an absolute Windows drive path to its WSL mount path.

    Args:
        value: Candidate path string.

    Returns:
        The translated ``/mnt/<drive>/...`` path when ``value`` starts with an
        ASCII drive letter, colon, and path separator; otherwise ``value``
        unchanged.
    """
    match = _WINDOWS_DRIVE_PATH_RE.match(value)
    if match is None:
        return value
    drive = match.group("drive").lower()
    rest = match.group("rest").replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def linked_worktree_gitdir(cwd: str) -> str | None:
    """Read a Windows-shaped linked-worktree gitdir pointer.

    Args:
        cwd: Directory whose direct ``.git`` entry should be inspected.

    Returns:
        The translated absolute gitdir path when ``.git`` is a pointer file
        containing a Windows drive path; otherwise ``None``. Read failures are
        treated as no matching pointer.
    """
    git_marker = Path(cwd) / ".git"
    try:
        if not git_marker.is_file():
            return None
        lines = git_marker.read_text().splitlines()
    except (OSError, UnicodeError):
        return None

    first_line = next((line for line in lines if line.strip()), None)
    if first_line is None or not first_line.startswith("gitdir:"):
        return None
    value = first_line.removeprefix("gitdir:").strip()
    if not value:
        return None
    normalized = normalize_wsl_drive_path(value)
    return normalized if normalized != value else None


def _first_git_marker_ancestor(cwd: str) -> Path | None:
    """Return the first ancestor containing a ``.git`` marker."""
    current = Path(cwd).absolute()
    for _ in range(_MAX_PARENT_LEVELS):
        marker = current / ".git"
        try:
            marker_exists = marker.exists()
        except OSError:
            return None
        if marker_exists:
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def linked_worktree_gitdir_from_ancestors(cwd: str) -> str | None:
    """Find a Windows-shaped linked-worktree gitdir pointer above ``cwd``.

    Repository discovery stops at the first ancestor containing any ``.git``
    marker, matching git's ownership boundary for the working tree.
    """
    work_tree = _first_git_marker_ancestor(cwd)
    if work_tree is None:
        return None
    return linked_worktree_gitdir(str(work_tree))


def git_dir_override(cwd: str) -> dict[str, str]:
    """Build git environment overrides for a Windows-created worktree.

    Starting at ``cwd``, the search walks toward the filesystem root and stops
    at the first directory containing any ``.git`` marker. A bounded level
    count supplements the root check so malformed path behavior cannot make the
    search loop forever.

    Args:
        cwd: Directory from which git repository discovery would begin.

    Returns:
        ``GIT_DIR`` and ``GIT_WORK_TREE`` for a translated pointer, or an empty
        dictionary for every ordinary repository shape.
    """
    work_tree = _first_git_marker_ancestor(cwd)
    if work_tree is None:
        return {}
    gitdir = linked_worktree_gitdir_from_ancestors(str(work_tree))
    if gitdir is None:
        return {}
    return {"GIT_DIR": gitdir, "GIT_WORK_TREE": str(work_tree)}
