"""One destination for public promotion, applied after caption composition.

Media attributes are not promotional links: the HTML normalizer preserves
image/video sources while removing old anchors and their promotional labels.
"""
from html import escape, unescape
from html.parser import HTMLParser
import re
import unicodedata

CANONICAL_URL = "https://linktr.ee/ILoveMyCats"
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\[\]{}\"']+|(?<![\w@])(?:[a-z0-9-]+\.)+(?:com|net|org|io|ee|jp|tv|me)(?:/[^\s<>\[\]{}\"']*)?", re.I)
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://|www\.)[^)]+\)", re.I)
_SERVICE = re.compile(
    r"patreon|youtube|youtu\.be|twitter|linktree|linktr\.ee|"
    r"(?:follow|更新|限定|作品|動画|写真|登録|リンク|誘導|続き).{0,24}(?:pixiv|tumblr|instagram|facebook|tiktok)|"
    r"(?:pixiv|tumblr|instagram|facebook|tiktok).{0,24}(?:follow|更新|限定|作品|動画|写真|登録|リンク|誘導|続き)", re.I)
_PROMO = re.compile(
    r"more\s+(?:exclusive\s+)?(?:content|on|photos|videos)|exclusive\s+content|"
    r"(?:all\s+)?sites?\s*(?:&|and)?\s*(?:gallery\s*)?hub|"
    r"(?:watch|see|view|check|visit|subscribe|support|follow|join)\b.{0,45}(?:channel|full|more|here|us|me|on|link|gallery)|"
    r"(?:links?|details?)\s*(?:in|below|here|:)|"
    r"related\s*(?:sites?|links?|blogs?|content|:|：)|recommended\s+(?:sites?|links?|blogs?|content)|"
    r"(?:プロフィール|プロフ|bio).{0,20}(?:リンク|link)|"
    r"(?:リンク|link).{0,20}(?:プロフィール|プロフ|bio)|"
    r"(?:関連|おすすめ|お勧め).{0,8}(?:サイト|ブログ)|"
    r"(?:更新|続き|詳細|リンク|限定|未公開|写真をもっと|全作品|全サイト|各サイト).{0,20}(?:こちら|ここ|へ|公開|チェック|見る|まとめ)|"
    r"(?:X|ツイッター)\s*(?:で|を|も|:|：|→)|FANZA.{0,30}(?:作品|こちら|へ|チェック|限定)", re.I)
_DISCLOSURE = re.compile(r"AI[- ]?(?:generated|生成)|架空|成人|18\+|all characters|fictional|R-?18", re.I)


def canonical_cta(html=False):
    if html:
        return f'<p><a href="{CANONICAL_URL}" target="_blank" rel="noopener">Links</a></p>'
    return CANONICAL_URL


def _is_legacy_promotion_heading(text):
    """Recognize the complete legacy sales heading, not ordinary service mentions."""
    return bool(re.fullmatch(
        r"[\s★☆]*My\s+contents?\s*[!！]*\s*"
        r"\(\s*I\s+(?:have\s+)?started\s+Gumroad"
        r"(?:\s+to\s+sell\s+(?:NSFW\s+)?contents?)?\s*[!！]*\s*\)[\s★☆!！]*",
        unescape(str(text)), re.IGNORECASE,
    ))


def _promotion_match(text, evidence=None, linked_evidence=False):
    """A generic check invitation needs explicit promotion evidence in its clause."""
    for match in _PROMO.finditer(text):
        if re.match(r"check\s+out\b", match.group(), re.IGNORECASE):
            source = text if evidence is None else evidence
            boundaries = list(re.finditer(r"(?<=[。!?！？])\s+|(?<=\.)\s+(?=[A-Z])", source))
            start = max((item.end() for item in boundaries if item.end() <= match.start()), default=0)
            end = min((item.start() for item in boundaries if item.start() > match.start()), default=len(source))
            if not _SERVICE.search(source[start:end]) and not linked_evidence:
                continue
        return match
    return None


def strip_plain_promotion(text, linked_evidence=()):
    """Remove destinations and promotional clauses without adding a CTA."""
    text = str(text or "")
    text = _MARKDOWN_LINK.sub("", text)
    kept = []
    for line in text.splitlines():
        if _is_legacy_promotion_heading(line):
            continue
        # Separate ordinary artwork/disclosure sentences from a trailing CTA.
        clauses = re.split(r"(?<=[。!?！？])\s+|(?<=\.)\s+(?=[A-Z])", line)
        clean = []
        clause_offset = 0
        for clause in clauses:
            clause_start = line.find(clause, clause_offset)
            clause_offset = clause_start + len(clause)
            linked_clause = any(clause_start <= position <= clause_offset for position in linked_evidence)
            original_clause = clause
            had_url = bool(_URL.search(clause))
            clause = _URL.sub("", clause).strip()
            if not clause:
                continue
            if _SERVICE.search(clause) or _promotion_match(clause, original_clause, linked_clause):
                # A disclosure may share a line with a service CTA. Keep its
                # earlier clause, never reintroduce the service destination.
                match = _promotion_match(clause, original_clause, linked_clause) or _SERVICE.search(clause)
                prefix = clause[:match.start()].rstrip(" |:\uff1a-\u2192\U0001f525\U0001f517")
                if _DISCLOSURE.search(prefix):
                    clean.append(prefix)
                elif re.match(r"check\s+out\b", match.group(), re.IGNORECASE) and not re.search(r"\w", prefix):
                    if any(unicodedata.category(char) == "So" for char in prefix):
                        clean.append(prefix)
                continue
            clause = clause.strip(" \t|:：→🔗🔥") if had_url else clause
            if clause and not re.fullmatch(r"[\W_]+", clause):
                clean.append(clause)
        if clean:
            kept.append(" ".join(clean))
        elif not line.strip():
            kept.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def normalize_plain_promotion(text, max_length=None):
    body = strip_plain_promotion(text)
    if max_length is not None:
        budget = max_length - len(CANONICAL_URL) - 2
        if budget < 0:
            raise ValueError("caption limit cannot contain the canonical destination")
        if len(body) > budget:
            # Preserve disclosure lines when clipping an oversized pool hook.
            lines = body.splitlines()
            disclosures = [line for line in lines if _DISCLOSURE.search(line)]
            ordinary = "\n".join(line for line in lines if not _DISCLOSURE.search(line)).strip()
            suffix = "\n".join(disclosures)
            reserved = len(suffix) + (2 if suffix and ordinary else 0)
            if len(suffix) > budget:
                raise ValueError("required disclosure exceeds caption limit")
            ordinary = ordinary[:max(0, budget - reserved)].rstrip()
            body = "\n\n".join(x for x in (ordinary, suffix) if x)
    return "\n\n".join(x for x in (body, CANONICAL_URL) if x)


class _Node:
    def __init__(self, tag="", attrs=(), data=""):
        self.tag, self.attrs, self.data, self.children = tag, list(attrs), data, []


class _PromotionHTML(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        # HTMLParser emits entity references separately. Rejoin adjacent text
        # so a URL query split at &amp; is removed as one complete URL.
        children = self.stack[-1].children
        if children and not children[-1].tag and not children[-1].children:
            children[-1].data += data
        else:
            children.append(_Node(data=data))

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")

    def handle_comment(self, data):
        # Operational marker comments are retained; public prose is unchanged.
        self.stack[-1].children.append(_Node("!comment", data=data))

    def render(self, node, linked_evidence=()):
        if node.tag == "!comment":
            return f"<!--{node.data}-->"
        if not node.tag and not node.children:
            cleaned = strip_plain_promotion(node.data, linked_evidence)
            if not cleaned:
                return " " if node.data.isspace() else ""
            return (" " if node.data[:1].isspace() else "") + cleaned + (" " if node.data[-1:].isspace() else "")
        if node.tag in {"p", "li", "h1", "h2", "h3", "h4"} and not self.has_media(node):
            # Related-site paragraphs contain several anchors separated by
            # labels and pipes. Recognize the block before removing anchors;
            # otherwise those unlabeled fragments become orphaned text.
            text = self.text_content(node)
            if _is_legacy_promotion_heading(text):
                return ""
            matches = [m for m in (_SERVICE.search(text), _promotion_match(text)) if m]
            if matches and not _DISCLOSURE.search(text):
                first = min(matches, key=lambda m: m.start())
                if not re.search(r"[\w\u3040-\u30ff\u4e00-\u9fff]", text[:first.start()]):
                    return ""
        known_links = list(linked_evidence) + self.known_link_positions(node)
        rendered = []
        offset = 0
        for child in node.children:
            rendered.append(self.render(child, [position - offset for position in known_links]))
            offset += len(self.text_content(child))
        children = "".join(rendered)
        if not node.tag:
            return children
        if node.tag == "a":
            # A linked thumbnail remains visible; all navigational anchors and
            # their labels are removed. Only resource descendants survive.
            return "".join(self.render(child) for child in node.children if self.has_media(child))
        if node.tag in {"p", "li", "h1", "h2", "h3", "h4", "strong", "span", "div"} and not children.strip():
            return ""
        attrs = "".join(f' {key}' if value is None else f' {key}="{escape(value, quote=True)}"' for key, value in node.attrs)
        opening = f"<{node.tag}{attrs}>"
        return opening if node.tag in self._VOID else opening + children + f"</{node.tag}>"

    def known_link_positions(self, node):
        positions = []
        offset = 0
        for child in node.children:
            if child.tag == "a" and any(key == "href" and value and _SERVICE.search(value) for key, value in child.attrs):
                positions.append(offset)
            positions.extend(offset + position for position in self.known_link_positions(child))
            offset += len(self.text_content(child))
        return positions

    def has_media(self, node):
        return node.tag in {"img", "video", "audio", "source", "iframe", "embed", "object"} or any(self.has_media(c) for c in node.children)

    def text_content(self, node):
        return node.data if not node.children else "".join(self.text_content(c) for c in node.children)


def normalize_html_promotion(html):
    parser = _PromotionHTML()
    parser.feed(str(html or ""))
    parser.close()
    return parser.render(parser.root).strip() + "\n" + canonical_cta(html=True)
