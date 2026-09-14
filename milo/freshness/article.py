"""Readable text extraction for fetched article pages."""

from html.parser import HTMLParser

from .util import as_text, collapse


MAX_ARTICLE_CHARS = 8000
DROP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "head", "noscript", "form", "svg"}
BLOCK_TAGS = {"address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption", "figure",
              "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "ol", "p", "pre", "section",
              "table", "td", "th", "tr", "ul"}


class _ReadableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.drop_depth = 0
        self.preferred_depth = 0
        self.all_text = []
        self.preferred_text = []

    def _separator(self):
        if self.drop_depth:
            return
        self.all_text.append(" ")
        if self.preferred_depth:
            self.preferred_text.append(" ")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth:
            return
        if tag in ("article", "main"):
            self.preferred_depth += 1
        if tag in BLOCK_TAGS:
            self._separator()

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in BLOCK_TAGS:
            self._separator()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth:
            return
        if tag in BLOCK_TAGS:
            self._separator()
        if tag in ("article", "main") and self.preferred_depth:
            self.preferred_depth -= 1

    def handle_data(self, data):
        if self.drop_depth:
            return
        self.all_text.append(data)
        if self.preferred_depth:
            self.preferred_text.append(data)


def extract_readable_text(page, limit=MAX_ARTICLE_CHARS):
    """Extract page prose, preferring article or main and removing common chrome."""
    parser = _ReadableHTMLParser()
    try:
        parser.feed(as_text(page))
        parser.close()
    except Exception:
        return ""
    preferred = collapse("".join(parser.preferred_text))
    fallback = collapse("".join(parser.all_text))
    return (preferred or fallback)[:limit]
