"""
CommonMark renderer for course pages (markdown-it-py).

The course material is written and previewed as CommonMark (Jupyter, GitHub,
VS Code), so rendering it with a CommonMark parser makes the site match what
authors see. Python-Markdown differs in ways that break real content:

* a list directly after a paragraph line is folded into the paragraph as
  literal "* item" text (CommonMark starts a list);
* nested lists need 4-space indentation (2-space nesting is flattened);
* an unclosed raw-HTML tag (e.g. `<div><embed ...>` with no `</div>`) turns
  the rest of the page into raw HTML.

Markdown inside raw HTML blocks (`<details>`, `<blockquote>`, `<div>`) works the
CommonMark way: an HTML block ends at the next blank line, and markdown resumes
after it. `_normalize_html_blocks` adds the blank lines the content sometimes
leaves out around `<details>` / `</summary>` / `</details>`, so nested
dropdowns and dropdowns inside comments need no regex pre-rendering.
"""

import html
import re
import unicodedata

from markdown.extensions.codehilite import CodeHilite
from markdown_it import MarkdownIt

_FENCE_RE = re.compile(r"^ {0,3}(?:>\s*)*(```|~~~)")
_SUMMARY_CLOSE_RE = re.compile(r"</summary>")
_DETAILS_CLOSE_RE = re.compile(r"^(\s*)</details>\s*$")
# A closing tag on its own line ends a raw-HTML block; markdown straight after it needs a blank line.
_BLOCK_CLOSE_RE = re.compile(r"^\s*</(?:div|blockquote|table)>\s*$")
_DETAILS_TAG_RE = re.compile(r"<details[\s>]|</details>")


def _highlight(code: str, lang: str | None, shebang: bool) -> str:
    """
    Highlight a code block exactly as the old renderer did, by reusing Python-Markdown's codehilite:
    same <div class="codehilite"> markup (so the site CSS applies) and the same language guess for
    blocks with no language.
    """
    return CodeHilite(code, lang=lang or None, guess_lang=True, css_class="codehilite", use_pygments=True).hilite(
        shebang=shebang
    )


def _render_fence(self, tokens, idx, options, env):
    token = tokens[idx]
    lang = token.info.strip().split()[0] if token.info.strip() else ""
    return _highlight(token.content, lang, shebang=False)


def _render_code_block(self, tokens, idx, options, env):
    return _highlight(tokens[idx].content, None, shebang=True)


def _slugify(value: str, separator: str = "-") -> str:
    """Python-Markdown's toc slugify, so heading anchors (and links to them) keep working."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(rf"[{separator}\s]+", separator, value)


def _heading_anchors(state) -> None:
    """
    Give every heading outside a <details> dropdown a unique id, like the toc extension did.

    Headings inside dropdowns get none, so they stay out of the page's sidebar
    table of contents (the old renderer rendered dropdowns separately, without toc).
    """
    used: set[str] = set()
    depth = 0
    tokens = state.tokens
    renderer = state.md.renderer
    placeholders = state.env.get("latex_placeholders", {})
    for i, token in enumerate(tokens):
        if token.type in ("html_block", "inline"):
            raw = token.content
            depth += len(re.findall(r"<details[\s>]", raw)) - raw.count("</details>")
            depth = max(depth, 0)
        if token.type != "heading_open" or depth > 0:
            continue
        inline = tokens[i + 1]
        text = renderer.renderInline(inline.children or [], state.md.options, state.env)
        for placeholder, latex in placeholders.items():
            text = text.replace(placeholder, latex)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        slug = base = _slugify(text) or "section"
        n = 0
        while slug in used:
            n += 1
            slug = f"{base}_{n}"
        used.add(slug)
        token.attrSet("id", slug)


def _normalize_html_blocks(text: str) -> str:
    """
    Add the blank lines CommonMark needs for markdown inside <details> dropdowns.

    A CommonMark HTML block runs until the next blank line, so
        <details><summary>Hint</summary> Use **this**.
    would leave "Use **this**." as raw HTML. We move anything after </summary>
    onto its own paragraph, and put blank lines after </summary> and around
    </details>, and after a closing </div>, </blockquote> or </table> line that
    markdown follows (e.g. the exercise-info boxes). Code fences are left alone.
    """
    out: list[str] = []
    in_fence = False
    fence = ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if m:
            if not in_fence:
                in_fence, fence = True, m.group(1)
            elif m.group(1) == fence:
                in_fence = False
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        indent = re.match(r"\s*", line).group(0)
        if "</summary>" in line:
            head, tail = line.split("</summary>", 1)
            out.append(head + "</summary>")
            out.append("")
            if tail.strip():
                out.append(indent + tail.strip())
                out.append("")
            continue
        if _DETAILS_CLOSE_RE.match(line):
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            out.append("")
            continue
        out.append(line)
        if _BLOCK_CLOSE_RE.match(line) and i + 1 < len(lines) and _starts_markdown(lines[i + 1]):
            out.append("")
    return "\n".join(out)


def _starts_markdown(line: str) -> bool:
    """Is this line markdown text (not blank, not more raw HTML, not indented enough to become a code block)?"""
    return bool(line.strip()) and not line.lstrip().startswith("<") and not line.startswith(("    ", "\t"))


def _make_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.add_render_rule("fence", _render_fence)
    md.add_render_rule("code_block", _render_code_block)
    md.core.ruler.push("heading_anchors", _heading_anchors)
    return md


_MD = _make_parser()


def render_commonmark(text: str, latex_placeholders: dict) -> str:
    """Render (already LaTeX-protected) markdown to HTML."""
    text = _normalize_html_blocks(text)
    return _MD.render(text, {"latex_placeholders": latex_placeholders})
