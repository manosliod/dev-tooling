#!/usr/bin/env python3
import ast
import bisect
import fnmatch
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

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

REGEX_PRECEDERS = set('(,=:[!&|?{};+-*%<>~^')
REGEX_KEYWORDS = {
    'return', 'typeof', 'case', 'do', 'else', 'in', 'of', 'new', 'delete', 'void', 'throw', 'yield', 'await',
    'instanceof',
}
YAML_BLOCK_OPENER = re.compile(r'(^|[:\-?])\s*[|>][+\-]?\d*\s*$')
CPP_RAW_PREFIX = re.compile(r'(?<![\w])(u8|u|U|L)?R$')


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str

    def render(self) -> str:
        text = self.text if len(self.text) <= 100 else self.text[:97] + '...'
        return f'{self.path}:{self.line}: {text}'


@dataclass(frozen=True)
class CStyle:
    line_comments: bool = True
    char_literals: bool = True
    digit_separators: bool = False
    raw_strings: bool = False
    text_blocks: bool = False
    backticks: bool = False
    regex: bool = False
    directives: tuple[str, ...] = ()


@dataclass(frozen=True)
class HashStyle:
    mode: str
    markers: tuple[str, ...] = ('#',)
    quote_boundary: bool = False
    triple_quotes: bool = False
    yaml_blocks: bool = False
    directives: tuple[str, ...] = ()


JAVA = CStyle(text_blocks=True, directives=JAVA_DIRECTIVES)
CPP = CStyle(digit_separators=True, raw_strings=True, directives=CPP_DIRECTIVES)
JS = CStyle(backticks=True, regex=True, directives=JS_DIRECTIVES)
JSON = CStyle(char_literals=False)
CSS = CStyle(line_comments=False, directives=CSS_DIRECTIVES)
YAML = HashStyle('space', quote_boundary=True, yaml_blocks=True, directives=YAML_DIRECTIVES)
TOML = HashStyle('any', quote_boundary=True, triple_quotes=True)
SHELL = HashStyle('space', directives=SHELL_DIRECTIVES)
LINE_START = HashStyle('line-start')
DOCKER = HashStyle('line-start', directives=DOCKER_DIRECTIVES)
INI = HashStyle('line-start', markers=('#', ';'))
PROPERTIES = HashStyle('line-start', markers=('#', '!'))
MAKE = HashStyle('any')

SUFFIX_STYLES = {
    '.java': JAVA,
    '.kt': JAVA,
    '.kts': JAVA,
    '.gradle': JAVA,
    '.c': CPP,
    '.h': CPP,
    '.cc': CPP,
    '.cpp': CPP,
    '.cxx': CPP,
    '.hh': CPP,
    '.hpp': CPP,
    '.hxx': CPP,
    '.ipp': CPP,
    '.js': JS,
    '.mjs': JS,
    '.cjs': JS,
    '.jsx': JS,
    '.ts': JS,
    '.mts': JS,
    '.cts': JS,
    '.tsx': JS,
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
    '.cmake': MAKE,
}
NAME_STYLES = {
    '.gitignore': LINE_START,
    '.gitattributes': LINE_START,
    '.dockerignore': LINE_START,
    '.no-comments-ignore': LINE_START,
    '.editorconfig': INI,
    '.clang-format': YAML,
    '.clang-tidy': YAML,
    '.env.example': SHELL,
    'Dockerfile': DOCKER,
    'Makefile': MAKE,
    'CMakeLists.txt': MAKE,
    'requirements.txt': SHELL,
}


def line_starts(text: str) -> list[int]:
    return [0] + [index + 1 for index, char in enumerate(text) if char == '\n']


def allowed(comment: str, directives: tuple[str, ...]) -> bool:
    return any(re.fullmatch(pattern, comment.strip()) for pattern in directives)


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else text


def scan_c_style(text: str, style: CStyle) -> list[tuple[int, str]]:
    starts = line_starts(text)
    found: list[tuple[int, str]] = []
    length = len(text)
    index = 0
    last_sig: str | None = None
    last_word = ''

    def record(start: int, end: int) -> None:
        comment = text[start:end]
        if not allowed(comment, style.directives):
            found.append((bisect.bisect_right(starts, start), first_line(comment)))

    def skip_quoted(position: int, quote: str) -> int:
        position += 1
        while position < length and text[position] != quote and text[position] != '\n':
            position += 2 if text[position] == '\\' else 1
        return position + 1

    def skip_template(position: int) -> int:
        position += 1
        depth = 0
        while position < length:
            char = text[position]
            if char == '\\':
                position += 2
                continue
            if depth == 0 and char == '`':
                return position + 1
            if char == '$' and text.startswith('{', position + 1):
                depth += 1
                position += 2
                continue
            if depth and char == '{':
                depth += 1
            elif depth and char == '}':
                depth -= 1
            position += 1
        return position

    def skip_regex(position: int) -> int:
        position += 1
        in_class = False
        while position < length and text[position] != '\n':
            char = text[position]
            if char == '\\':
                position += 2
                continue
            if char == '[':
                in_class = True
            elif char == ']':
                in_class = False
            elif char == '/' and not in_class:
                position += 1
                break
            position += 1
        while position < length and text[position].isalpha():
            position += 1
        return position

    def regex_allowed() -> bool:
        if last_sig is None or last_sig in REGEX_PRECEDERS:
            return True
        return last_sig == 'a' and last_word in REGEX_KEYWORDS

    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if style.line_comments and text.startswith('//', index):
            end = text.find('\n', index)
            end = length if end < 0 else end
            record(index, end)
            index = end
            continue
        if text.startswith('/*', index):
            end = text.find('*/', index + 2)
            end = length if end < 0 else end + 2
            record(index, end)
            index = end
            continue
        if char == '"':
            if style.text_blocks and text.startswith('"""', index):
                end = text.find('"""', index + 3)
                index = length if end < 0 else end + 3
            elif style.raw_strings and CPP_RAW_PREFIX.search(text, max(0, index - 3), index):
                open_paren = text.find('(', index)
                delimiter = text[index + 1:open_paren]
                end = text.find(')' + delimiter + '"', open_paren)
                index = length if open_paren < 0 or end < 0 else end + len(delimiter) + 2
            else:
                index = skip_quoted(index, '"')
            last_sig = '"'
            continue
        if char == "'" and style.char_literals:
            index = skip_quoted(index, "'")
            last_sig = '"'
            continue
        if char == '`' and style.backticks:
            index = skip_template(index)
            last_sig = '"'
            continue
        if char == '/' and style.regex and regex_allowed():
            index = skip_regex(index)
            last_sig = '"'
            continue
        if char.isalpha() or char in '_$':
            end = index
            while end < length and (text[end].isalnum() or text[end] in '_$'):
                end += 1
            last_word = text[index:end]
            last_sig = 'a'
            index = end
            continue
        if char.isdigit():
            end = index
            extra = "_.'" if style.digit_separators else '_.'
            while end < length and (text[end].isalnum() or text[end] in extra):
                end += 1
            last_sig = 'a'
            last_word = ''
            index = end
            continue
        last_sig = char
        index += 1
    return found


def scan_hash_style(text: str, style: HashStyle) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    block_indent: int | None = None
    open_triple: str | None = None

    def record(number: int, comment: str) -> None:
        if number == 1 and comment.startswith('#!'):
            return
        if not allowed(comment, style.directives):
            found.append((number, comment.strip()))

    for number, line in enumerate(text.split('\n'), 1):
        indent = len(line) - len(line.lstrip())
        if block_indent is not None:
            if not line.strip() or indent > block_indent:
                continue
            block_indent = None
        if style.mode == 'line-start':
            if line.lstrip().startswith(style.markers):
                record(number, line.strip())
            continue
        position = 0
        if open_triple:
            close = line.find(open_triple)
            if close < 0:
                continue
            position = close + 3
            open_triple = None
        quote: str | None = None
        code_end = len(line)
        while position < len(line):
            char = line[position]
            if quote:
                if quote == '"' and char == '\\':
                    position += 2
                    continue
                if char == quote:
                    if quote == "'" and line.startswith("'", position + 1):
                        position += 2
                        continue
                    quote = None
                position += 1
                continue
            if style.triple_quotes and line.startswith(('"""', "'''"), position):
                close = line.find(line[position:position + 3], position + 3)
                if close < 0:
                    open_triple = line[position:position + 3]
                    break
                position = close + 3
                continue
            if char in style.markers and (style.mode == 'any' or position == 0 or line[position - 1] in ' \t'):
                record(number, line[position:])
                code_end = position
                break
            if char in '"\'':
                before = line[:position].rstrip()
                if not style.quote_boundary or not before or before[-1] in ':-[{,=':
                    quote = char
            position += 1
        if style.yaml_blocks and YAML_BLOCK_OPENER.search(line[:code_end].rstrip()):
            block_indent = indent
    return found


def scan_python(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type != tokenize.COMMENT:
                continue
            if token.start == (1, 0) and token.string.startswith('#!'):
                continue
            if not allowed(token.string, PYTHON_DIRECTIVES):
                found.append((token.start[0], token.string))
    except (tokenize.TokenError, SyntaxError):
        found.extend(scan_hash_style(text, HashStyle('space', directives=PYTHON_DIRECTIVES)))
        return found
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            found.append((node.lineno, 'docstring: ' + first_line(node.value.value)))
    return sorted(found)


def blank_out(text: str, start: int, end: int) -> str:
    return text[:start] + re.sub(r'[^\n]', ' ', text[start:end]) + text[end:]


def scan_markup(text: str, django: bool) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    starts = line_starts(text)
    masked = text
    for match in re.finditer(r'<(script|style)\b([^>]*)>(.*?)</\1\s*>', text, re.S | re.I):
        tag, attributes, inner = match.group(1).lower(), match.group(2), match.group(3)
        if tag == 'style':
            style = CSS
        elif 'json' in attributes:
            style = JSON
        else:
            style = JS
        offset = bisect.bisect_right(starts, match.start(3)) - 1
        found.extend((line + offset, comment) for line, comment in scan_c_style(inner, style))
        masked = blank_out(masked, match.start(3), match.end(3))
    patterns = [r'<!--.*?-->']
    if django:
        patterns += [r'\{#.*?#\}', r'\{%\s*comment\b.*?%\}']
    for pattern in patterns:
        for match in re.finditer(pattern, masked, re.S):
            if not allowed(match.group(0), MARKUP_DIRECTIVES):
                found.append((bisect.bisect_right(starts, match.start()), first_line(match.group(0))))
    return sorted(found)


def find_comments(path: str, text: str, extra_allowed: tuple[str, ...] = ()) -> list[Finding]:
    name = Path(path).name
    suffix = Path(path).suffix.lower()
    if suffix == '.py' or (not suffix and text.startswith('#!') and 'python' in text.split('\n', 1)[0]):
        raw = scan_python(text)
    elif suffix in ('.vue', '.html', '.htm', '.xml', '.svg'):
        raw = scan_markup(text, django=suffix in ('.html', '.htm'))
    elif name in NAME_STYLES or name.startswith('Dockerfile'):
        style = NAME_STYLES.get(name, DOCKER)
        raw = scan_hash_style(text, style)
    elif suffix in SUFFIX_STYLES:
        style = SUFFIX_STYLES[suffix]
        raw = scan_c_style(text, style) if isinstance(style, CStyle) else scan_hash_style(text, style)
    elif not suffix and text.startswith('#!'):
        raw = scan_hash_style(text, SHELL)
    else:
        raw = []
    return [Finding(path, line, comment) for line, comment in raw if not allowed(comment, extra_allowed)]


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
    root = repo_root(Path.cwd()).resolve()
    relatives = [Path(argument).resolve().relative_to(root).as_posix() for argument in arguments]
    findings, checked = check(root, relatives or tracked_files(root))
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
