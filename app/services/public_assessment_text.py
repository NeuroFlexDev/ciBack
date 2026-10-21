"""Read-time privacy guard for historical case descriptions; never rewrites history."""
import re


# Earlier materialization appended the private exemplar at the end of the public
# task description. Old publication snapshots are immutable, so strip that suffix
# only when returning learner data. Reviewers retain the full original snapshot.
LEGACY_ANSWER = re.compile(r"(?:ожидаемый\s+ответ|expected\s+(?:answer|response))\s*(?:\*\*)?\s*:", re.IGNORECASE)


def public_assessment_text(value):
    if not isinstance(value, str):
        return value
    match = LEGACY_ANSWER.search(value)
    if match is None:
        return value
    return value[:match.start()].rstrip(" \t\r\n*#")
