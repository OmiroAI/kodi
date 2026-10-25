# Preparing a public source release

## What belongs in this project

Reusable coordinator code, generic examples, tests, public provider/dependency
references and intentional project/license/maintainer metadata belong here.
Operator infrastructure, private application behavior, repository inventories,
credentials, personal paths, runtime transcripts, private deployment integrations
and application-specific dependency graphs do not. Maintain those in private
storage or the relevant private application repository.

Use `ExampleOrg/project`, reserved example domains, generic role names and
RFC 5737 documentation addresses. The existing project name, copyright/license,
maintainer CODEOWNERS and compatibility directory/image defaults are public
project metadata; they do not identify an operator deployment.

## Current files and automated checks

```sh
python3 scripts/check_public_source.py
python3 -m unittest discover -s tests -v
```

The checker reports file/line and disclosure category, never the matched value.
It rejects credential/state/archive filenames without opening them, common token
and private-key shapes, internal/mesh domains, personal home paths, and private
or shared addresses. Loopback, unspecified bind addresses and documentation
addresses are allowed. An optional private JSON list of literal identifiers adds
operator-specific checks without committing a list of private names:

```sh
python3 scripts/check_public_source.py --private-patterns /private/path/patterns.json
```

The check is a guardrail, not a secret detector or a replacement for manual review.
Review private product names, screenshots, binary metadata, dependency lists,
comments, commit subjects, PR descriptions and links before a public release.
No private deployment data should be pasted into public reports of a failed check.

## Git history and GitHub metadata

Deleting a file or changing a hostname in the current tree does not remove it
from earlier commits, branches, tags, PR diffs, issue discussions, releases,
artifacts, forks or caches. A clean scan of the current tree is not permission
to make an existing history-bearing repository public.

The safest release path for a previously private operational repository is a
fresh public repository created from a reviewed source-only export. Keep the
original repository and private operations history private. This avoids carrying
old commits, remotes, contributor email metadata and private PR discussions into
the new public project. A history rewrite alone does not remove all GitHub-side
metadata. Changes to visibility, force pushes or publication require explicit
owner authorization after the release contents and destination are reviewable.

```sh
python3 scripts/export_public.py --output /outside/source/kodi-public \
  --private-patterns /private/path/patterns.json
```

The tool requires a clean, reviewed tree, refuses existing destinations, runs the
privacy check, and copies tracked source only. Its archive omits Git data and
normalizes ownership/timestamps. It never creates a remote, uploads an artifact,
changes repository visibility or executes a deployment. Review the export before
initializing fresh public Git history and selecting a publication destination.

## Existing installations

The cleanup does not require moving live services or deleting private operations.
Retain a private backup before extracting application-specific adapters. Keep the
current private checkout available while migrating those adapters separately.
Configure application QA commands/images in the private QA-host profile file,
and keep actual repository catalogs in operator configuration outside Git.
Generic default paths/tags remain compatible with existing coordinator installs.
GitHub setup now requires the operator organization and repository as environment
inputs; do not restore hardcoded deployment values to make an installation work.
