#!/usr/bin/env python3
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tree_sitter_language_pack import Node, get_parser

IGNORE_FILE = '.no-comments-ignore'
ALLOW_FILE = '.no-comments-allow'
MAX_BYTES = 1_000_000

DEFAULT_IGNORES = (
    'docs/*',
    '.no-comments-allow',
    '*.md',
    'LICENSE*',
    'gradlew',
    'gradlew.bat',
    'gradle/wrapper/*',
    '*/migrations/*.py',
    'migrations/*.py',
    '*.lock',
    'pnpm-lock.yaml',
    'package-lock.json',
    'yarn.lock',
    '*.min.js',
    '*.min.css',
)

RULES = r'[\w@/\-.]+(\s*,\s*[\w@/\-.]+)*'
PRETTIER = (r'//\s*prettier-ignore', r'/\*\s*prettier-ignore\s*\*/')
ESLINT = (
    r'//\s*eslint-disable(-next-line|-line)?(\s+' + RULES + r')?',
    r'/\*\s*eslint-(disable|enable)(-next-line|-line)?(\s+' + RULES + r')?\s*\*/',
)
PYTHON_DIRECTIVES = (
    r'#\s*noqa(:\s*[A-Z]+[0-9]+(\s*,\s*[A-Z]+[0-9]+)*)?',
    r'#\s*ruff:\s*noqa(:\s*[A-Z]+[0-9]+(\s*,\s*[A-Z]+[0-9]+)*)?',
    r'#\s*type:\s*ignore(\[[\w\-, ]+\])?',
    r'#\s*pyright:\s*(ignore(\[[\w\-, ]+\])?|basic|strict|standard)',
    r'#\s*mypy:\s*[\w\-=, ]+',
    r'#\s*pragma:\s*no (cover|branch)',
    r'#\s*fmt:\s*(off|on|skip)',
    r'#\s*isort:\s*(skip|skip_file|off|on)',
    r'#\s*nosec(\s+B\d+(,B\d+)*)?',
    r'#\s*-\*-\s*coding:\s*[\w\-]+\s*-\*-',
)
JS_DIRECTIVES = (
    *ESLINT,
    *PRETTIER,
    r'//\s*@ts-(expect-error|ignore|nocheck|check)',
    r'///\s*<reference\s+(types|path|lib)="[^"]+"\s*/>',
    r'/\*\s*@vite-ignore\s*\*/',
    r'/\*\s*(webpackChunkName|webpackPrefetch|webpackPreload|webpackIgnore):\s*[^*]+\*/',
    r'/\*\s*[@#]__(PURE|NO_SIDE_EFFECTS)__\s*\*/',
    r'/\*\s*istanbul\s+ignore\s+(next|if|else|file)\s*\*/',
    r'/\*\s*v8\s+ignore\s+(next|start|stop)\s*\*/',
    r'//\s*@vitest-environment\s+[\w\-]+',
    r'/\*\*\s*@vitest-environment\s+[\w\-]+\s*\*/',
)
CSS_DIRECTIVES = (
    r'/\*\s*prettier-ignore\s*\*/',
    r'/\*\s*stylelint-(disable|enable)(-next-line|-line)?(\s+' + RULES + r')?\s*\*/',
)
CPP_DIRECTIVES = (
    r'//\s*NOLINT(NEXTLINE|BEGIN|END)?(\([\w\-.,*]+\))?',
    r'//\s*clang-format\s+(off|on)',
    r'/\*\s*clang-format\s+(off|on)\s*\*/',
    r'//\s*IWYU\s+pragma:\s*[\w\-<>"./ ]+',
    r'//\s*cppcheck-suppress\s+[\w\-]+',
)
JAVA_DIRECTIVES = (
    r'//\s*@formatter:(off|on)',
    r'//\s*spotless:(off|on)',
    r'//\s*CHECKSTYLE[.:]\s*(OFF|ON)(:\s*\w+)?',
    r'//\s*noinspection\s+[\w,]+',
    r'//\s*NOPMD(\s*-\s*[\w,]+)?',
)
SHELL_DIRECTIVES = (r'#\s*shellcheck\s+(disable|source|shell)=[\w,./\-]+',)
YAML_DIRECTIVES = (
    r'#\s*yaml-language-server:\s*\$schema=\S+',
    r'#\s*yamllint\s+(disable|enable)(-line)?(\s+rule:[\w\-]+)*',
    r'#\s*zizmor:\s*ignore\[[\w\-, ]+\]',
)
DOCKER_DIRECTIVES = (r'#\s*(syntax|escape|check)=\S+',)
MARKUP_DIRECTIVES = (
    r'<!--\s*prettier-ignore(-start|-end)?\s*-->',
    r'<!--\s*eslint-(disable|enable)(-next-line|-line)?(\s+' + RULES + r')?\s*-->',
    r'<!--\s*htmlmin:ignore\s*-->',
    r'\{#\s*djlint:\s*(off|on)\s*#\}',
    r'\{#\s*djlint:\s*ignore\s+[\w,\-]+\s*#\}',
    r'<\?xml[^>]*\?>',
)
RELEASE_PLEASE_MARKERS = (
    r'(#|//|/\*|<!--)\s*x-release-please-(start-)?(version|major|minor|patch)\s*(\*/|-->)?',
    r'(#|//|/\*|<!--)\s*x-release-please-end\s*(\*/|-->)?',
)

COMMENT_TYPES = frozenset(
    {
        'comment',
        'line_comment',
        'block_comment',
        'multiline_comment',
        'bracket_comment',
        'Comment',
        'unpaired_comment',
        'paired_comment',
    }
)
STATEMENT_PARENTS = frozenset({'module', 'block'})
EMBEDDED = frozenset({'script_element', 'style_element'})


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str

    def render(self) -> str:
        text = self.text if len(self.text) <= 100 else self.text[:97] + '...'
        return f'{self.path}:{self.line}: {text}'


@dataclass(frozen=True)
class Language:
    grammar: str
    directives: tuple[str, ...] = ()


PYTHON = Language('python', PYTHON_DIRECTIVES)
JAVA = Language('java', JAVA_DIRECTIVES)
KOTLIN = Language('kotlin', JAVA_DIRECTIVES)
GROOVY = Language('groovy', JAVA_DIRECTIVES)
C = Language('c', CPP_DIRECTIVES)
CPP = Language('cpp', CPP_DIRECTIVES)
JAVASCRIPT = Language('javascript', JS_DIRECTIVES)
TYPESCRIPT = Language('typescript', JS_DIRECTIVES)
TSX = Language('tsx', JS_DIRECTIVES)
JSON = Language('json')
CSS = Language('css', CSS_DIRECTIVES)
YAML = Language('yaml', YAML_DIRECTIVES)
TOML = Language('toml')
SHELL = Language('bash', SHELL_DIRECTIVES)
INI = Language('ini')
EDITORCONFIG = Language('editorconfig')
PROPERTIES = Language('properties')
CMAKE = Language('cmake')
MAKE = Language('make')
DOCKER = Language('dockerfile', DOCKER_DIRECTIVES)
GITIGNORE = Language('gitignore')
GITATTRIBUTES = Language('gitattributes')
REQUIREMENTS = Language('requirements')
HTML = Language('html', MARKUP_DIRECTIVES)
DJANGO = Language('htmldjango', MARKUP_DIRECTIVES)
VUE = Language('vue', MARKUP_DIRECTIVES)
XML = Language('xml', MARKUP_DIRECTIVES)

SUFFIX_LANGUAGES = {
    '.py': PYTHON,
    '.java': JAVA,
    '.kt': KOTLIN,
    '.kts': KOTLIN,
    '.gradle': GROOVY,
    '.c': C,
    '.h': CPP,
    '.cc': CPP,
    '.cpp': CPP,
    '.cxx': CPP,
    '.hh': CPP,
    '.hpp': CPP,
    '.hxx': CPP,
    '.ipp': CPP,
    '.js': JAVASCRIPT,
    '.mjs': JAVASCRIPT,
    '.cjs': JAVASCRIPT,
    '.jsx': JAVASCRIPT,
    '.ts': TYPESCRIPT,
    '.mts': TYPESCRIPT,
    '.cts': TYPESCRIPT,
    '.tsx': TSX,
    '.json': JSON,
    '.jsonc': JSON,
    '.css': CSS,
    '.yml': YAML,
    '.yaml': YAML,
    '.toml': TOML,
    '.sh': SHELL,
    '.bash': SHELL,
    '.env': SHELL,
    '.cfg': INI,
    '.ini': INI,
    '.properties': PROPERTIES,
    '.cmake': CMAKE,
    '.vue': VUE,
    '.html': HTML,
    '.htm': HTML,
    '.xml': XML,
    '.svg': XML,
}
NAME_LANGUAGES = {
    '.gitignore': GITIGNORE,
    '.dockerignore': GITIGNORE,
    '.no-comments-ignore': GITIGNORE,
    '.gitattributes': GITATTRIBUTES,
    '.editorconfig': EDITORCONFIG,
    '.clang-format': YAML,
    '.clang-tidy': YAML,
    '.env.example': SHELL,
    'Makefile': MAKE,
    'CMakeLists.txt': CMAKE,
    'requirements.txt': REQUIREMENTS,
}


def allowed(comment: str, directives: tuple[str, ...]) -> bool:
    return any(re.fullmatch(pattern, comment.strip()) for pattern in directives)


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text


def language_for(path: str, text: str) -> Language | None:
    name = Path(path).name
    suffix = Path(path).suffix.lower()
    if name in NAME_LANGUAGES:
        return NAME_LANGUAGES[name]
    if name.startswith('Dockerfile'):
        return DOCKER
    if suffix in SUFFIX_LANGUAGES:
        return SUFFIX_LANGUAGES[suffix]
    if not suffix and text.startswith('#!'):
        return PYTHON if 'python' in text.split('\n', 1)[0] else SHELL
    return None


def parse(grammar: str, source: bytes) -> Node:
    return get_parser(grammar).parse(source).root_node


def embedded_language(element: Node) -> Language:
    if element.type == 'style_element':
        return CSS
    start = next((child for child in element.children if child.type == 'start_tag'), None)
    attributes = start.text.decode() if start and start.text else ''
    if 'json' in attributes:
        return JSON
    if re.search(r'lang\s*=\s*["\']tsx["\']', attributes):
        return TSX
    if re.search(r'lang\s*=\s*["\']ts["\']', attributes):
        return TYPESCRIPT
    return JAVASCRIPT


def docstring(node: Node) -> str | None:
    value = node
    if node.type == 'expression_statement' and node.named_child_count == 1:
        value = node.named_children[0]
    elif node.parent is None or node.parent.type not in STATEMENT_PARENTS:
        return None
    if value.type == 'string':
        strings = [value]
    elif value.type == 'concatenated_string':
        strings = value.named_children
    else:
        return None
    if any(part.type != 'string' for part in strings):
        return None
    if any(child.type == 'interpolation' for part in strings for child in part.named_children):
        return None
    content = ''.join(
        (child.text or b'').decode() for part in strings for child in part.named_children if child.type == 'string_content'
    )
    return 'docstring: ' + first_line(content)


def collect(root: Node, language: Language, offset: int = 0) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        line = node.start_point[0] + 1 + offset
        if node.type in COMMENT_TYPES:
            text = (node.text or b'').decode()
            comment = text.lstrip()
            line += text[: len(text) - len(comment)].count('\n')
            if line == 1 and comment.startswith('#!'):
                continue
            if not allowed(comment, language.directives + RELEASE_PLEASE_MARKERS):
                found.append((line, first_line(comment)))
            continue
        if language is PYTHON and (text := docstring(node)):
            found.append((line, text))
            continue
        if node.type in EMBEDDED:
            raw = next((child for child in node.children if child.type == 'raw_text'), None)
            if raw is not None and raw.text:
                inner = embedded_language(node)
                found.extend(collect(parse(inner.grammar, raw.text), inner, raw.start_point[0] + offset))
            continue
        stack.extend(reversed(node.children))
    return found


def django_comments(source: bytes) -> list[tuple[int, str]]:
    return [
        (line, comment)
        for line, comment in collect(parse(DJANGO.grammar, source), DJANGO)
        if comment.startswith('{')
    ]


def find_comments(path: str, text: str, extra_allowed: tuple[str, ...] = ()) -> list[Finding]:
    language = language_for(path, text)
    if language is None:
        return []
    source = text.encode()
    raw = collect(parse(language.grammar, source), language)
    if language is HTML:
        raw.extend(django_comments(source))
    return [
        Finding(path, line, comment) for line, comment in sorted(set(raw)) if not allowed(comment, extra_allowed)
    ]


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def load_ignores(root: Path) -> list[str]:
    return [*DEFAULT_IGNORES, *read_lines(root / IGNORE_FILE)]


def is_ignored(relative: str, ignores: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in ignores)


def repo_root(start: Path) -> Path:
    result = subprocess.run(
        ['git', '-C', str(start), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=False
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else start


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, text=True, check=True)
    return [path for path in result.stdout.split('\0') if path]


def read_text(path: Path) -> str | None:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        return None
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return None


def check(root: Path, relatives: list[str]) -> tuple[list[Finding], int]:
    ignores = load_ignores(root)
    extra_allowed = tuple(read_lines(root / ALLOW_FILE))
    findings: list[Finding] = []
    checked = 0
    for relative in relatives:
        if is_ignored(relative, ignores):
            continue
        text = read_text(root / relative)
        if text is None:
            continue
        checked += 1
        findings.extend(find_comments(relative, text, extra_allowed))
    return findings, checked


def run_hook() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    file_path = (payload.get('tool_input') or {}).get('file_path')
    if not file_path:
        return 0
    path = Path(file_path).resolve()
    if not path.is_file():
        return 0
    root = repo_root(path.parent).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return 0
    findings, _ = check(root, [relative])
    if not findings:
        return 0
    lines = [finding.render() for finding in findings]
    print(
        'Comments written for people are banned here. Remove these; put any explanation in docs/. '
        'If one is a directive a tool needs, add its pattern to .no-comments-allow:',
        file=sys.stderr,
    )
    print('\n'.join(lines), file=sys.stderr)
    return 2


def main(arguments: list[str]) -> int:
    if arguments[:1] == ['--hook']:
        return run_hook()
    groups: dict[Path, list[str]] = {}
    for argument in arguments:
        path = Path(argument).resolve()
        root = repo_root(path.parent).resolve()
        groups.setdefault(root, []).append(path.relative_to(root).as_posix())
    if not groups:
        root = repo_root(Path.cwd()).resolve()
        groups[root] = tracked_files(root)
    findings: list[Finding] = []
    checked = 0
    for root, relatives in groups.items():
        found, count = check(root, relatives)
        findings.extend(found)
        checked += count
    for finding in findings:
        print(finding.render())
    if findings:
        print(
            f'\n{len(findings)} comment(s) written for people. Move what they say to docs/ and delete them. '
            f'If one is a directive a tool needs, add its pattern to {ALLOW_FILE}.'
        )
        return 1
    print(f'No comments found in {checked} file(s).')
    return 0


def cli() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == '__main__':
    cli()
