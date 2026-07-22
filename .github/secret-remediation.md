# Secret remediation after repository cleanup

Removing sensitive files from the current branch does not remove their contents
from existing commits, forks, clones, CI logs, or caches.

## Required rotation

Treat every credential previously committed in `.env.dev`, `.env.stage`, and
`.env.prod` as compromised. Rotate, in the owning service, at least:

- application JWT/authentication secrets;
- database users and passwords;
- SMTP credentials;
- Hugging Face and other external-provider tokens.

Record the owner and completion date outside the repository. Revoke old values
after deployments have switched to the replacements.

## Optional history rewrite

History rewriting is a coordinated repository-administration operation. Do not
run it until maintainers have agreed on a maintenance window, protected-branch
handling, backup location, and notification to every contributor.

After that approval, use a fresh mirror clone and `git filter-repo` to remove
the affected paths from every ref:

```bash
git clone --mirror <repository-url> lernium-cleanup.git
cd lernium-cleanup.git
git filter-repo --invert-paths \
  --path .env.dev \
  --path .env.stage \
  --path .env.prod \
  --path database.db \
  --path cert/sber.pem
git push --force --mirror
```

Afterward, invalidate caches and artifacts, rescan the rewritten history, and
require contributors to re-clone or carefully rebase onto the rewritten refs.
Rotation is still mandatory even when the rewrite succeeds.
