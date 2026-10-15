# dev-tooling

Shared CI workflows and checks for the repositories of [manosliod](https://github.com/manosliod). Each repository
calls them from here, pinned to a version, instead of keeping its own copy.

## What is here

| Name | Kind | What it does |
| --- | --- | --- |
| `no-comments` | Reusable workflow and Python command | Fails when a tracked file has a comment written for people |

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
    uses: manosliod/dev-tooling/.github/workflows/no-comments.yml@v1
```

The workflow checks out the calling repository, then this repository at the exact commit that `@v1` points to, and
runs the check.

### Run it on your machine

From anywhere inside a repository:

```bash
uvx --from git+https://github.com/manosliod/dev-tooling@v1 check-no-comments
uvx --from git+https://github.com/manosliod/dev-tooling@v1 check-no-comments src/app.ts src/main.py
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

## Versions

Releases are tagged `vMAJOR.MINOR.PATCH`, and a moving `vMAJOR` tag points to the newest release of that major
version. Repositories pin `@v1`, so fixes reach them without a change on their side. A change that could make a
passing repository fail, such as a new language or a removed directive, is a new major version.

To release:

```bash
git tag v1.0.1
git tag -f v1
git push origin v1.0.1
git push -f origin v1
```

## Develop

```bash
uv run python -m unittest discover -s tests
uv run check-no-comments
```

The first line runs the tests. The second runs the check on this repository itself, which CI also does through the
same reusable workflow other repositories use.

## Licence

[MIT](LICENSE)
