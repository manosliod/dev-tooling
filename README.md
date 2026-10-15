# dev-tooling

Shared CI workflows and checks for the repositories of [manosliod](https://github.com/manosliod). Each repository
calls them from here, pinned to a version, instead of keeping its own copy.

## What is here

| Name | Kind | What it does |
| --- | --- | --- |
| `no-comments` | Reusable workflow and Python command | Fails when a tracked file has a comment written for people |
| `release-please` | Reusable workflow | Opens release pull requests, writes `CHANGELOG.md`, tags releases |
| `pr-title` | Reusable workflow | Fails when a pull request title is not a [Conventional Commit](https://www.conventionalcommits.org/) |

## no-comments

The code in these repositories has no comments written for people: no descriptions, no history, no narration.
What the code cannot say goes into plain-language docs in `docs/`. Comments that a tool reads, like `# noqa` or
`// prettier-ignore`, are fine. This check finds the first kind and lets the second kind through.

**What it does.** It reads every file tracked by git and reports each comment as `file:line: text`. If it finds any,
it exits with an error, and that makes CI fail.

**How it avoids false alarms.** It does not guess with patterns. Each file is parsed by the
[tree-sitter](https://tree-sitter.github.io/) grammar of its language, from
[`tree-sitter-language-pack`](https://pypi.org/project/tree-sitter-language-pack/), and only what the grammar calls a
comment is checked. So `"http://x"` in a string, `#fff` in a quoted colour, or a regular expression like `/\/\//` are
never counted. Scripts and styles inside HTML and Vue files are parsed with their own grammar.

**First run.** The grammars are not inside the Python package. The first run downloads them once (about 23 MB, from
the package's GitHub releases) into `~/.cache/tree-sitter-language-pack/`, and every later run uses that copy. The
reusable workflow caches that folder between CI runs. If the download fails, the check fails; it never passes
without having read the files.

**Languages.** Python (including docstrings), Java, Kotlin, Gradle, C and C++, JavaScript, TypeScript and TSX, Vue,
HTML and Django templates, XML and SVG, CSS, JSON/JSONC, YAML, TOML, shell and `.env`, INI, `.editorconfig`,
`.properties`, CMake, Make, Dockerfile, `requirements.txt`, `.gitignore`, `.gitattributes` and similar. Other files are
skipped. A new language is one line in `SUFFIX_LANGUAGES` or `NAME_LANGUAGES`, as long as the language pack has a
grammar for it.

**Allowed lines.** Comments that a tool reads, written with nothing after them. For example:

| Language | Allowed |
| --- | --- |
| Any script | A shebang on line 1 (`#!/usr/bin/env python3`) |
| Python | `# noqa: F401`, `# type: ignore[...]`, `# pyright: ignore[...]`, `# fmt: off/on/skip`, `# isort: skip`, `# nosec`, `# pragma: no cover`, `# -*- coding: utf-8 -*-` |
| JS / TS / Vue | `// eslint-disable-next-line rule`, `/* eslint-disable */`, `// @ts-expect-error`, `// prettier-ignore`, `/* @vite-ignore */`, `/* webpackChunkName: "x" */`, `/* @__PURE__ */`, `/// <reference types="..." />`, `// @vitest-environment x` |
| CSS | `/* stylelint-disable-next-line rule */`, `/* prettier-ignore */` |
| Java | `// spotless:off/on`, `// @formatter:off/on`, `//noinspection X`, `// CHECKSTYLE:OFF` |
| C / C++ | `// NOLINT(check)`, `// NOLINTNEXTLINE(check)`, `// clang-format off/on`, `// IWYU pragma: keep` |
| Shell | `# shellcheck disable=SC1234` |
| YAML | `# yaml-language-server: $schema=...`, `# yamllint disable-line` |
| Dockerfile | `# syntax=docker/dockerfile:1`, `# escape=\`` |
| Templates | `<!-- prettier-ignore -->`, `{# djlint:off #}` |

The full list is in `src/check_no_comments/__init__.py`, in the `*_DIRECTIVES` lists.

**A tool comment the check does not know?** Add a regular expression for it, one per line, to `.no-comments-allow`
at the root of the repository being checked. A comment that fully matches a line there is allowed. Example line:
`#\s*route:\s*\S+`.

**Skipped files.** `docs/`, Markdown, licence files, lock files, minified files, the Gradle wrapper and Django
migrations, because tools write those. A repository can skip more by listing globs, one per line, in
`.no-comments-ignore`.

### Use it in CI

Add this job under `jobs:` in the repository's workflow:

```yaml
  no-comments:
    uses: manosliod/dev-tooling/.github/workflows/no-comments.yml@v1.2
```

The workflow checks out the calling repository, then this repository at the exact commit that `@v1.2` points to, and
runs the check.

### Run it on your machine

From anywhere inside a repository:

```bash
uvx --from git+https://github.com/manosliod/dev-tooling@v1.2 check-no-comments
uvx --from git+https://github.com/manosliod/dev-tooling@v1.2 check-no-comments src/app.ts src/main.py
```

The first line checks the whole repository. The second checks only the files you name.

### Use it as a Claude Code hook

With this repository cloned and [uv](https://docs.astral.sh/uv/) installed, Claude Code can run the check after every file it edits. With `--hook`, the check reads
the edited path from the hook input and blocks the change if the file has a comment:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --quiet --project \"$HOME/Sites/manosliod/dev-tooling\" check-no-comments --hook",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

## release-please

[release-please](https://github.com/googleapis/release-please-action) reads the commits that land on the default
branch. When there is something to release, it opens a pull request that raises the version and adds the new entries
to `CHANGELOG.md`. Merging that pull request tags the release (`v1.2.0`) and publishes a GitHub release with the same
notes.

The commit type decides the version and where the entry goes:

| Commit | Version | Changelog section |
| --- | --- | --- |
| `fix: ...` | patch | Bug Fixes |
| `feat: ...` | minor | Features |
| `feat!: ...` or a `BREAKING CHANGE:` footer | major | the section of its type, marked breaking |
| `perf:`, `refactor:`, `revert:` | patch | Performance, Refactoring, Reverts |
| `docs:`, `test:`, `build:`, `ci:`, `chore:`, `style:` | none on their own | hidden |

Pull requests are squash-merged, so each one becomes one commit whose message is the pull request title. That is why
`pr-title` checks the title.

**Why a GitHub App.** A pull request opened with the workflow's own `GITHUB_TOKEN` does not start other workflows, so
the release pull request would never get the checks that branch protection requires. The workflow therefore opens it
with a token from a GitHub App, made by
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token).

### Set up a repository

1. Install the release GitHub App on the repository.
2. Add the variable `RELEASE_APP_CLIENT_ID` and the secret `RELEASE_APP_PRIVATE_KEY`:

   ```bash
   gh variable set RELEASE_APP_CLIENT_ID --body "<client id>"
   gh secret set RELEASE_APP_PRIVATE_KEY < path/to/private-key.pem
   ```

3. Add `release-please-config.json` and `.release-please-manifest.json` at the root. The config holds the release type
   and the changelog sections; this repository's own config is an example. The manifest holds the current version,
   e.g. `{ ".": "0.1.0" }`.
4. Add the workflow:

   ```yaml
   name: Release

   on:
     push:
       branches:
         - master

   permissions: {}

   jobs:
     release-please:
       uses: manosliod/dev-tooling/.github/workflows/release-please.yml@v1.2
       with:
         app-client-id: ${{ vars.RELEASE_APP_CLIENT_ID }}
       secrets:
         app-private-key: ${{ secrets.RELEASE_APP_PRIVATE_KEY }}
   ```

The workflow returns `release_created`, `tag_name`, `version`, `major` and `minor`, so a later job can deploy or
publish only when a release was made.

**A version outside the rules.** A commit with a `Release-As: 1.0.0` line in its body makes the next release that
version.

**Version markers in files.** release-please updates a version in a file it does not know when the line ends with a
`x-release-please-version` comment, e.g. `project(app VERSION 1.0.0) # x-release-please-version` in `CMakeLists.txt`,
listed under `extra-files` in the config. `no-comments` allows these markers.

## pr-title

Uses [`amannn/action-semantic-pull-request`](https://github.com/amannn/action-semantic-pull-request) to fail a pull
request whose title is not `type: subject` or `type(scope): subject`, with one of the standard Conventional Commit
types.

```yaml
name: Pull request

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions:
  pull-requests: read

jobs:
  pr-title:
    uses: manosliod/dev-tooling/.github/workflows/pr-title.yml@v1.2
```

## Versions

Each release is tagged `vMAJOR.MINOR.PATCH`. Two more tags move with it:

| Tag | Points to | Who uses it |
| --- | --- | --- |
| `v1.2` | the newest `v1.2.x` | Repositories: they get fixes on their own, and nothing that could make them fail |
| `v1` | the newest `v1.x.y` | Only kept so older callers keep working |
| `v1.2.0` | one release, never moves | Anyone who wants an exact version |

What each part of the version means:

- **Patch** (`v1.2.1`): a fix that can never make a passing repository fail, such as fewer false alarms or a clearer
  message.
- **Minor** (`v1.3.0`): anything that could make a passing repository fail, such as a new language, a new check or a
  directive that is no longer allowed. Each repository moves to it in its own pull request, which shows whether it
  still passes.
- **Major** (`v2.0.0`): a change in how the workflow is called, such as a renamed workflow or a new required input.

Releases are made by this repository's own `release-please` workflow: `fix:` gives a patch, `feat:` a minor and a
breaking change a major. A minor that can make a passing repository fail is still a minor, even when its commit is a
`fix:`; give it the version with a `Release-As:` line. After each release, the `move-tags` job points `vMAJOR.MINOR`
and `vMAJOR` at it, so a new minor creates `v1.2` and leaves `v1.1` where it was.

## Develop

```bash
uv run python -m unittest discover -s tests
uv run check-no-comments
```

The first line runs the tests. The second runs the check on this repository itself, which CI also does through the
same reusable workflow other repositories use.

## Licence

[MIT](LICENSE)
