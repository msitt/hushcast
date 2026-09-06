# changelog.d

Pending release notes live here, one file per change. At release time
`.github/scripts/prepare_release.py` combines them into a new version section
at the top of `CHANGELOG.md` and deletes them.

Entries are never written directly into `CHANGELOG.md`. That file is only ever
written by the release workflow.

## Adding an entry

Create `changelog.d/<category>-<slug>.md`, where `<category>` is one of the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) sections:

`added`, `changed`, `deprecated`, `removed`, `fixed`, `security`

The file holds the markdown bullet for the change, leading `- ` included:

```markdown
- Search for podcasts by name when adding one. The Add podcast dialog now
  searches the Apple Podcasts directory as you type.
```

A fragment can hold more than one bullet if a change really needs it. Within a
category, fragments are rendered in filename order.

## Writing entries

Write for end users. Say what changed and why it matters, not how it was built.
Leave out route names, module names, and other internals unless they matter to
someone using or deploying hushcast. Err on the succinct side.

## What needs an entry

New features, significant behavior changes, and important bugfixes. Not needed
for styling tweaks, copy adjustments, internal refactors, or test-only changes.

## Checking your work

```bash
python3 .github/scripts/prepare_release.py --check
```

CI runs this too. It validates the fragments that exist and passes when there
are none, which is the normal state right after a release.
