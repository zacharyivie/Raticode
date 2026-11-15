"""Folders Raticode creates and manages inside its data folder.

This is the one list of them. Workflow discovery skips them, so a checkout or scratch
folder that Rem or an organization keeps here can't show up as a workflow, and bundle
import refuses to write assets into them. tests/unit/test_data_folders.py fails when code
names a new folder in the data folder that is neither here nor on its short allow-list.
"""

from __future__ import annotations

MANAGED_DATA_FOLDERS = frozenset(
    {
        ".gofer-chat-prompts",
        "agent-memory",
        "approvals",
        "browser-grants",
        "chat-attachments",
        "chat-change-blobs",
        "chat-change-sets",
        "chat-context",
        "chat-jobs",
        "chat-provider-sessions",
        "chat-steering",
        "command-slots",
        "devices",
        "generation-jobs",
        "headless",
        "logs",
        "node-cache",
        "organization-repositories",
        "organization-runs",
        "organization-shared-worktrees",
        "organization-skills",
        "organization-worktrees",
        "radish",
        # Saved answers of nodes with cache: DURATION, one folder per workflow.
        "rattish-cache",
        "rattish-triggers",
        "rem-work",
        "rem-worktrees",
        "run-state",
        "second-brains",
        # Run retention settings (retention.json).
        "settings",
        # Rem's skill library: a planted SKILL.md would become instructions in Rem's threads.
        "skills",
        "speech-models",
        "trash",
        "usage-probe",
        "verification",
        "workflow-revisions",
        "workspaces",
    }
)
# Every chat-* folder belongs to Rem's chats, including ones added later.
MANAGED_DATA_FOLDER_PREFIXES = ("chat-",)


def is_managed_data_folder(name: str) -> bool:
    """Whether a top-level entry of the data folder is one Raticode manages.

    Case is ignored, as on the case-insensitive file systems of macOS and Windows.
    """
    folded = name.lower()
    return folded in MANAGED_DATA_FOLDERS or folded.startswith(MANAGED_DATA_FOLDER_PREFIXES)
