"""Shared commit-message instructions for Rem chat and commit drafting."""

DEFAULT_COMMIT_MESSAGE_TEMPLATE = (
    "fix | feat | test | chore: one line high level executive summary of changes. "
    "Only absolutely necessary technical terms; no jargon.\n\n"
    " - one line summary of most important change\n"
    " - one line summary of second most important change\n"
    " - ...\n"
    " - one line summary of last important change"
)
MAX_COMMIT_CHANGES = 8
MAX_COMMIT_TEMPLATE_LENGTH = 4000


def commit_message_instructions(template: str) -> str:
    return (
        "Whenever you write a commit message, follow the user's configured template below. "
        "Treat it as a format example, replacing placeholders with actual changes. "
        "Use a one-line subject followed by a blank line and concise, one-line change bullets. "
        f"Include between 1 and {MAX_COMMIT_CHANGES} change bullets, never more than "
        f"{MAX_COMMIT_CHANGES}. Combine related changes instead of omitting important work. "
        "Do not pad the list or print ellipses or placeholder text. "
        "In the default template, choose exactly one of fix, feat, test, or chore, "
        "and use plain bullets ordered by importance without numbered Change labels. "
        "A custom template may change the subject and bullet format, but keep the bullet limit.\n"
        f"Commit message template:\n{template}\n"
    )
