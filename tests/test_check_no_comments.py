import unittest

from check_no_comments import find_comments


def lines(path: str, text: str) -> list[int]:
    return [finding.line for finding in find_comments(path, text)]


class PythonTest(unittest.TestCase):
    def test_flags_comments_and_docstrings(self) -> None:
        text = 'def f():\n    """Doc."""\n    return 1  # why\n'
        self.assertEqual(lines('a.py', text), [2, 3])

    def test_f_strings_and_assigned_strings_are_not_docstrings(self) -> None:
        self.assertEqual(lines('a.py', 'x = 1\nf"{x}"\ny = "text"\n'), [])

    def test_ignores_hash_inside_strings(self) -> None:
        self.assertEqual(lines('a.py', "url = 'http://x/#top'\ncolor = \"#fff\"\n"), [])

    def test_allows_shebang_and_bare_directives(self) -> None:
        text = '#!/usr/bin/env python3\nimport os  # noqa: F401\nx = y  # type: ignore[name-defined]\n'
        self.assertEqual(lines('a.py', text), [])

    def test_rejects_directive_with_explanation(self) -> None:
        self.assertEqual(lines('a.py', 'import os  # noqa: F401 used by plugins\n'), [1])


class CStyleTest(unittest.TestCase):
    def test_java(self) -> None:
        text = 'class A {\n  // line\n  /** doc */\n  String s = "// not";\n  String t = """\n  /* no */\n  """;\n}\n'
        self.assertEqual(lines('A.java', text), [2, 3])

    def test_cpp_raw_strings_and_digit_separators(self) -> None:
        text = 'auto a = R"x(// no)x";\nint n = 1\'000\'000; // yes\nchar c = \'/\';\n#include <x> /* yes */\n'
        self.assertEqual(lines('a.cpp', text), [2, 4])

    def test_kotlin_and_gradle(self) -> None:
        self.assertEqual(lines('A.kt', 'val s = "// no"\n// c\n/* d */\n'), [2, 3])
        self.assertEqual(lines('build.gradle', 'id "x" // c\n'), [1])

    def test_tsx(self) -> None:
        self.assertEqual(lines('a.tsx', 'const a = <a href="http://x">{/* c */}</a>\n'), [1])

    def test_cpp_nolint(self) -> None:
        self.assertEqual(lines('a.cpp', 'int x = 0; // NOLINT(bugprone-foo)\n'), [])

    def test_js_regex_template_and_urls(self) -> None:
        text = (
            'const re = /\\/\\//g\n'
            'const u = `http://${host}/x` // yes\n'
            'const d = a / b / c\n'
            "const s = '/* no */'\n"
            'return /\\/*x/.test(s)\n'
        )
        self.assertEqual(lines('a.ts', text), [2])

    def test_js_directives(self) -> None:
        text = (
            '/// <reference types="vite/client" />\n'
            '// eslint-disable-next-line no-console\n'
            '// @ts-expect-error\n'
            '// eslint-disable-next-line no-console because reasons\n'
        )
        self.assertEqual(lines('a.ts', text), [4])

    def test_jsonc_and_css(self) -> None:
        self.assertEqual(lines('tsconfig.json', '{\n  "a": "//x", // c\n  /* b */ "b": 1\n}\n'), [2, 3])
        self.assertEqual(lines('a.css', 'a { background: url(http://x/y.png); } /* c */\n'), [1])


class MarkupTest(unittest.TestCase):
    def test_vue_blocks(self) -> None:
        text = (
            '<script setup lang="ts">\n'
            "const a = 'x' // c\n"
            '</script>\n'
            '<template>\n'
            '  <!-- c -->\n'
            '  <a href="http://x">x</a>\n'
            '</template>\n'
            '<style>\n'
            '/* c */\n'
            '</style>\n'
        )
        self.assertEqual(lines('A.vue', text), [2, 5, 9])

    def test_html_script_and_style(self) -> None:
        text = '<p>x</p>\n<script>\nconst a = "//no" // c\n</script>\n<style>\n/* s */\n</style>\n'
        self.assertEqual(lines('index.htm', text), [3, 6])

    def test_svg(self) -> None:
        self.assertEqual(lines('icon.svg', '<?xml version="1.0"?>\n<!-- c -->\n<svg/>\n'), [2])

    def test_django_template(self) -> None:
        text = '{# c #}\n<p>{{ a }}</p>\n{% comment %}x{% endcomment %}\n'
        self.assertEqual(lines('index.html', text), [1, 3])


class HashStyleTest(unittest.TestCase):
    def test_yaml(self) -> None:
        text = (
            'name: CI # c\n'
            "title: 'a # b'\n"
            "owner: Manos's app\n"
            'color: a#b\n'
            '# c\n'
            'run: |\n'
            '  echo "x" # shell inside block scalar\n'
            'next: 1\n'
        )
        self.assertEqual(lines('ci.yml', text), [1, 5])

    def test_toml(self) -> None:
        text = 'a = "# no"\nb = """\n# no\n"""\nc = 1 # yes\n'
        self.assertEqual(lines('pyproject.toml', text), [5])

    def test_shell(self) -> None:
        text = '#!/bin/sh\necho $# "${#x}"\necho hi # c\n# shellcheck disable=SC2086\n'
        self.assertEqual(lines('run.sh', text), [3])

    def test_gitignore_and_editorconfig(self) -> None:
        self.assertEqual(lines('.gitignore', '# c\nbuild/\n\\#file\n'), [1])
        self.assertEqual(lines('.editorconfig', '; c\nroot = true\n'), [1])

    def test_build_and_config_files(self) -> None:
        self.assertEqual(lines('CMakeLists.txt', 'set(A "#no") # c\n#[[ b ]]\n'), [1, 2])
        self.assertEqual(lines('Makefile', 'all: x # c\n\techo "#no"\n'), [1])
        self.assertEqual(lines('app.properties', '# c\na=b\n'), [1])
        self.assertEqual(lines('requirements.txt', 'django==5.2 # c\n'), [1])
        self.assertEqual(lines('.gitattributes', '# c\n*.sh text eol=lf\n'), [1])

    def test_unknown_files_are_skipped(self) -> None:
        self.assertEqual(lines('notes.txt', '# anything'), [])



class ToolDirectiveTest(unittest.TestCase):
    def test_python_tool_comments_pass_and_descriptions_fail(self) -> None:
        text = (
            '# -*- coding: utf-8 -*-\n'
            'x = [  # fmt: skip\n'
            '    1,\n'
            ']\n'
            "path('admin/', admin.site.urls),  # nosec\n"
            '# Admin route for the staff dashboard\n'
        )
        self.assertEqual(lines('urls.py', text), [6])

    def test_js_tool_comments(self) -> None:
        text = (
            '/* eslint-disable no-console */\n'
            '// prettier-ignore\n'
            'const m = import(/* @vite-ignore */ path)\n'
            'const x = /* @__PURE__ */ make()\n'
            '// @vitest-environment happy-dom\n'
            '// loads the user list\n'
        )
        self.assertEqual(lines('a.ts', text), [6])

    def test_other_languages(self) -> None:
        self.assertEqual(lines('A.java', '// spotless:off\nint x;\n// spotless:on\n// sets x\n'), [4])
        self.assertEqual(lines('a.cpp', '// clang-format off\nint x;\n// clang-format on\n'), [])
        self.assertEqual(lines('a.css', '/* stylelint-disable-next-line color-no-hex */\n/* header */\n'), [2])
        self.assertEqual(lines('ci.yml', '# yaml-language-server: $schema=https://x/s.json\na: 1\n'), [])
        self.assertEqual(lines('Dockerfile', '# syntax=docker/dockerfile:1\n# build stage\nFROM x\n'), [2])
        self.assertEqual(lines('a.vue', '<template>\n  <!-- prettier-ignore -->\n  <!-- card -->\n</template>\n'), [3])
        self.assertEqual(lines('base.html', '{# djlint:off #}\n{# nav bar #}\n'), [2])

    def test_release_please_markers(self) -> None:
        self.assertEqual(lines('CMakeLists.txt', 'project(a VERSION 1.0.0) # x-release-please-version\n'), [])
        self.assertEqual(lines('a.ts', "export const v = '1.0.0' // x-release-please-version\n"), [])
        self.assertEqual(lines('a.py', '# x-release-please-start-version\nV = "1"\n# x-release-please-end\n'), [])
        self.assertEqual(lines('a.py', 'V = "1"  # x-release-please-version bumped by CI\n'), [1])

    def test_repo_allow_patterns(self) -> None:
        findings = find_comments('a.py', '# route: /movies\n# a note\n', (r'#\s*route:\s*\S+',))
        self.assertEqual([finding.line for finding in findings], [2])

if __name__ == '__main__':
    unittest.main()
