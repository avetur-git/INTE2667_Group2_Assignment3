"""Syntax-check the front-end JavaScript with `node --check`.

Checks every inline <script> block in templates/ and every file in static/js/.
A syntax error stops a whole <script> block, so one bad line can break every
panel on a page without any server-side test noticing. Used by CI.

    python scripts/check_js_syntax.py      (needs Node.js on PATH)
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INLINE_SCRIPT = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)
JINJA = re.compile(r'{{.*?}}|{%.*?%}', re.S)


def node_check(path, label):
    result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
    if result.returncode == 0:
        return True
    # Show node's message (file:line, the line, the caret and the error) under our label
    detail = result.stderr.replace(str(path), label).strip()
    print(f'FAIL {label}\n{detail}\n')
    return False


def check_templates(tmp):
    ok = True
    for template in sorted((ROOT / 'templates').glob('*.html')):
        html = template.read_text(encoding='utf-8')
        for n, match in enumerate(INLINE_SCRIPT.finditer(html), 1):
            # Pad with newlines so node's line numbers match the template's
            line_offset = html.count('\n', 0, match.start(1))
            js = JINJA.sub(lambda m: 'null' if m.group().startswith('{{') else '', match.group(1))
            block = tmp / f'{template.stem}_{n}.js'
            block.write_text('\n' * line_offset + js, encoding='utf-8')
            ok &= node_check(block, f'templates/{template.name}')
    return ok


def main():
    with tempfile.TemporaryDirectory() as tmp:
        ok = check_templates(Path(tmp))
    for script in sorted((ROOT / 'static' / 'js').glob('*.js')):
        ok &= node_check(script, f'static/js/{script.name}')
    print('All front-end JavaScript parses.' if ok else 'JavaScript syntax errors found.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
