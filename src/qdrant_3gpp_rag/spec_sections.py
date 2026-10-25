"""
Chunk a 3GPP specification by SECTION: one chunk per clause.

A 3GPP spec is a tree of numbered clauses ("12.3.1.2.3.2.1.3.1 POST"), most of them
short (median ~250 characters in TS 28.532), each holding the text and the tables
that belong to it. Chunking by size cut that tree at arbitrary points: one chunk
ended a section and began the next, a table was split in two, and a deep clause
lost the title that said what it was about. Here the unit is the clause:

  - a chunk is one section — its heading, its text and its tables, never two
    sections; headings of every level are split points (ATX "#" .. "#########" and
    setext "===" / "---"), as 3GPP clauses run nine levels deep;
  - the contents listing ("Contents", in every 3GPP spec) is left out: a list of
    titles and page numbers that would match every query;
  - a heading with no text of its own ("12.3.1.2.3 Resources") is not a chunk: it
    is a parent, and every chunk starts with its nearest parents' titles
    ("12.3.1.2.3.2.1 Resource "/notificationSink" › 12.3.1.2.3.2.1.3 HTTP methods"),
    so a "POST" clause says which resource it is the method of;
  - only a section longer than `max_chars` is divided — first one YAML type
    definition per part (annex OpenAPI documents), then by paragraph — and every
    part repeats the section's heading and parents, marked "(part i/n)"; a part
    holding one YAML type defines it ("NotificationHeader:" -> NotificationHeader)
    when the name looks like code, or when the spec's own YAML refers to it as a
    type ("#/components/schemas/Uri" makes "Uri:" a definition — the markdown lost
    the YAML indentation, so a bare "Location:" header key looks the same);
  - everything is indexed, annexes included, every chunk marked annex=True/False: a
    3GPP spec's annexes hold its finished OpenAPI / YANG / ASN.1 documents, which
    other specs' documents reference (TS 28.532's YAML takes NotificationHeader from
    TS 28.623 Annex C, its only definition). Which annexes a task may read is the
    consumer's decision (filter on annex / spec), not this package's. Left out: the
    cover page (the text before the first heading) and the contents listing;
  - each chunk carries its clause, title and parent path, so a consumer can filter
    or group by clause, plus:
      clause_ancestors — the clause and all its prefixes ("12", "12.3", ...), so an
                         exact match on "12.3" selects the clause and its subtree;
      defines          — what the section defines, read from a 3GPP-style title
                         ("Type NotifyThresholdCrossing", "Enumeration: DataInd",
                         'Resource "/notificationSink"'), the clean name only;
                         a section whose own title defines nothing inherits the
                         name of its nearest defining parent when that parent has
                         no text of its own ("11.1.1.10 Notification notifyEvent" is
                         split into "Definition" and "Input parameters": both
                         define notifyEvent);
      mentions         — the names defined elsewhere in the SAME spec that the
                         section cites (a light graph: fetch the definition of each
                         type a section uses with a filter defines = X).

The markdown is kept as markdown (no HTML round trip, which dropped the heading
markers) and tables stay where they are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from langchain_text_splitters import RecursiveCharacterTextSplitter

# Default upper bound of a chunk, in characters. 4000 keeps whole all but the longest
# ~8% of TS 28.532's sections (a type definition table is ~2.5k).
SECTION_MAX_CHARS = 4000
SECTION_OVERLAP = 200
# How many parent titles a chunk starts with.
PARENTS_IN_TEXT = 2
CHUNKING = "sections-v3"           # recorded on every chunk: how it was cut
# Upper bound of a chunk's `mentions` list (first occurrences kept).
MAX_MENTIONS = 30

_ATX = re.compile(r"^(#{1,9})[ \t]+(?P<title>[^\n]*?)[ \t#]*$", re.MULTILINE)
_SETEXT = re.compile(r"^(?P<title>[^\n|+]*\S[^\n|+]*)\n(?P<rule>=+|-{3,})[ \t]*$", re.MULTILINE)
_ATTRS = re.compile(r"\s*\{#[^}]*\}\s*$")
_CLAUSE = re.compile(
    r"^(?:Annex\s+)?(?P<clause>\d+[a-z]?(?:\.\d+[a-z]?)*|[A-Z](?:\.\d+[a-z]?)+|[A-Z]\b)")
_ANNEX = re.compile(r"^Annex\s+[A-Z]\b", re.IGNORECASE)
_YAML_TYPE_DEF = re.compile(r"^[A-Z][A-Za-z0-9]+:\s*$", re.MULTILINE)
_SCHEMA_REF = re.compile(r"components/schemas/([A-Za-z0-9_.-]+)")
# A defining title: clause, kind, optional colon, then the name. Two styles are seen:
# "Type NotifyThresholdCrossing", 'Resource \"/notificationSink\"' (TS 28.xxx) and
# "Type: ReplaceHttpUrl", "Resource: Individual Subscription (Document)" (TS 29.xxx).
_DEFINING_TITLE = re.compile(
    r"^(?:\d+[a-z]?(?:\.\d+[a-z]?)*|[A-Z](?:\.\d+[a-z]?)+)\s+"
    r"(?:Type|Enumeration|Notification|Operation|Resource)(?P<colon>\s*:)?\s+(?P<rest>.+)$")
_QUOTED = re.compile(r'^"(?P<name>[^"]+)"')
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_TRAILING_PARENS = re.compile(r"\s*\([^)]*\)\s*$")
# The shape of a code identifier: a lower-case letter followed by a capital
# ("notifyEvent", "createMOI", "NfGroupIds"), or a digit / "_" / "-" / "/". A bare
# word after a kind ("Resource allocation", "Operation Points", "Notification PUSH")
# is prose, not a name; only the colon and quoted styles may name one in words.
_CODE_SHAPE = re.compile(r"[a-z][A-Z]|[/_\-\d]")


@dataclass
class Section:
    clause: str                    # "12.3.1.2.3.2.1.3.1", "A.1.1", "" (Foreword, front matter)
    title: str                     # "12.3.1.2.3.2.1.3.1 POST"
    heading: str                   # the heading line as written, without {#attrs}
    body: str                      # the text under the heading, up to the next heading
    level: int                     # heading level (1 = "#" / "===", 2 = "##" / "---" ...)
    annex: bool = False
    parents: List[str] = field(default_factory=list)   # titles, outermost first
    # What the nearest defining parent defines, when it has no text of its own
    # (its content is this section and its siblings); [] otherwise.
    parent_defines: List[str] = field(default_factory=list)


def clause_of(title: str) -> str:
    """The clause number a heading starts with, '' if none ('Foreword')."""
    m = _CLAUSE.match(title.strip().strip("*").strip())
    return m.group("clause") if m else ""


def clause_ancestors(clause: str) -> List[str]:
    """The clause and all its prefixes, outermost first: '12.3.1' -> ['12', '12.3',
    '12.3.1']; [] for no clause."""
    parts = clause.split(".") if clause else []
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def defines_of(title: str) -> List[str]:
    """What a 3GPP-style title defines, as a clean name: 'Type: ReplaceHttpUrl' ->
    ['ReplaceHttpUrl'], 'Resource \\"/notificationSink\\"' -> ['/notificationSink'];
    [] for a title that defines nothing ('Resource structure')."""
    m = _DEFINING_TITLE.match(title.replace('\\"', '"').strip())
    if not m:
        return []
    rest = m.group("rest").strip()
    quoted = _QUOTED.match(rest)
    if quoted:
        name = quoted.group("name").strip().strip("\\").strip()
        return [name] if name else []
    rest = _TRAILING_PARENS.sub("", rest).strip()     # "relocate (POST)", "X (Document)"
    if not rest or rest.lower() == "void":            # "Type: Void" — a deleted clause
        return []
    if m.group("colon"):                               # "Resource: Individual Subscription"
        return [rest] if re.match(r"[A-Za-z0-9/.]", rest) else []
    return [rest] if _IDENTIFIER.match(rest) and _CODE_SHAPE.search(rest) else []


def _mention_pattern(names: Set[str]) -> Optional[re.Pattern]:
    """One regex finding any code-shaped name, longest first, as a whole word. Names
    that are ordinary words ("delete", "Individual subscription") are not searched:
    they would make every section a hit."""
    usable = sorted((n for n in names if _CODE_SHAPE.search(n)), key=lambda n: (-len(n), n))
    if not usable:
        return None
    alts = "|".join(re.escape(n) for n in usable)
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alts})(?![A-Za-z0-9_])")


def mentions_in(text: str, pattern: Optional[re.Pattern], own: List[str],
                limit: int = MAX_MENTIONS) -> List[str]:
    """The spec's defined names cited in `text`, in order of first occurrence, without
    the section's own and at most `limit`."""
    if pattern is None:
        return []
    found: List[str] = []
    for m in pattern.finditer(text.replace('\\"', '"')):
        name = m.group(0)
        if name not in own and name not in found:
            found.append(name)
            if len(found) >= limit:
                break
    return found


_FENCE = re.compile(r"^[ \t]*(```|~~~)[^\n]*$", re.MULTILINE)


def _fenced_spans(text: str) -> List[tuple]:
    """(start, end) of every fenced code block (``` or ~~~), unclosed ones to the end."""
    marks = [m.start() for m in _FENCE.finditer(text)]
    if len(marks) % 2:
        marks.append(len(text))
    return list(zip(marks[0::2], marks[1::2]))


def _headings(text: str, skip_code_fences: bool = False) -> List[tuple]:
    """(start, end, level, title) of every heading, in document order. With
    skip_code_fences, a "# comment" line inside a fenced code block is not one."""
    found = [(m.start(), m.end(), len(m.group(1)), m.group("title")) for m in _ATX.finditer(text)]
    for m in _SETEXT.finditer(text):
        line = m.group("title").strip()
        if line.startswith(("#", ">", "-", "*", "|")) or not re.search(r"[A-Za-z0-9]", line):
            continue                                   # not a heading: a list, quote, rule
        found.append((m.start(), m.end(), 1 if m.group("rule")[0] == "=" else 2, line))
    if skip_code_fences:
        spans = _fenced_spans(text)
        found = [h for h in found if not any(a <= h[0] < b for a, b in spans)]
    found.sort()
    out, last_end = [], -1
    for start, end, level, title in found:
        if start >= last_end:                         # an ATX line inside a setext block
            out.append((start, end, level, _ATTRS.sub("", title).strip().strip("*").strip()))
            last_end = end
    return out


def _is_parent(parent: Section, child_clause: str, child_level: int) -> bool:
    if parent.clause and child_clause:
        return child_clause.startswith(parent.clause + ".")
    return parent.level < child_level


def split_sections(text: str, skip_code_fences: bool = False) -> List[Section]:
    """Every section of a spec, in order, each knowing its parents and whether it is
    in an annex. The text before the first heading is a section titled
    '(front matter)'. skip_code_fences: see _headings (off for the 3GPP corpus,
    whose index was built without it)."""
    heads = _headings(text, skip_code_fences)
    sections: List[Section] = []
    if heads and text[:heads[0][0]].strip():
        sections.append(Section(clause="", title="(front matter)", heading="",
                                body=text[:heads[0][0]].strip(), level=0))
    stack: List[Section] = []
    in_annex = False
    for i, (start, end, level, title) in enumerate(heads):
        body_end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        clause = clause_of(title)
        in_annex = in_annex or bool(_ANNEX.match(title))
        while stack and not _is_parent(stack[-1], clause, level):
            stack.pop()
        # Only the NEAREST defining parent counts: a section under "Type Y" (with
        # text) under 'Resource "/x"' (without) belongs to Y, not to /x.
        definer = next((s for s in reversed(stack) if defines_of(s.title)), None)
        parent_defines = defines_of(definer.title) if definer and not definer.body else []
        sec = Section(clause=clause, title=title, heading=_ATTRS.sub("", text[start:end]).strip(),
                      body=text[end:body_end].strip(), level=level, annex=in_annex,
                      parents=[s.title for s in stack], parent_defines=parent_defines)
        sections.append(sec)
        stack.append(sec)
    return sections


def _prefix(sec: Section) -> str:
    near = sec.parents[-PARENTS_IN_TEXT:]
    return (" › ".join(near) + "\n\n") if near else ""


def schema_refs(text: str) -> Set[str]:
    """The type names a spec's YAML refers to: "#/components/schemas/<name>" (markdown
    escapes such as "\\#" removed)."""
    return set(_SCHEMA_REF.findall(text.replace("\\", "")))


def _pieces(body: str, room: int, overlap: int,
            schema_names: Optional[Set[str]] = None) -> List[tuple]:
    """A long body divided: one YAML type definition per piece when it holds several,
    then by paragraph; never larger than `room`. Each piece comes with the YAML type
    it holds (every piece of a type split further keeps its name), or None."""
    starts = [m.start() for m in _YAML_TYPE_DEF.finditer(body)]
    units = ([body[a:b].strip() for a, b in zip([0] + starts[1:], starts[1:] + [len(body)])]
             if len(starts) >= 2 else [body])
    splitter = RecursiveCharacterTextSplitter(chunk_size=max(room, 200), chunk_overlap=overlap,
                                              separators=["\n\n", "\n", " ", ""])
    out: List[tuple] = []
    for unit in units:
        # search, not match: the first unit also holds the document's preamble
        # ("openapi: 3.0.1 ... components: schemas:") before its type
        head = _YAML_TYPE_DEF.search(unit) if len(starts) >= 2 else None
        name = head.group(0).rstrip().rstrip(":") if head else None
        if name and not _CODE_SHAPE.search(name) and name not in (schema_names or ()):
            name = None                 # "Example:", "Location:" — not shown to be a type
        for piece in ([unit] if len(unit) <= room else splitter.split_text(unit)):
            if piece.strip():
                out.append((piece, name))
    return out


def chunk_spec_sections(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    max_chars: int = SECTION_MAX_CHARS,
    overlap: int = SECTION_OVERLAP,
    include_annexes: bool = True,
    schema_names: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Segments ready for index_segments(..., chunk_size=None): one per section (or
    per part of a long one), text under "text", plus the given metadata and
    clause / clause_ancestors / title / path / defines / mentions / annex / part /
    parts / chunking. The cover page and the contents listing are left out; the
    annexes too when include_annexes=False. schema_names: type names known from the
    rest of the spec (its other files); this text's own references are always used."""
    base = dict(metadata or {})
    known = schema_refs(text) | set(schema_names or ())
    kept: List[tuple] = []        # (section, [(text, body, names defined by the part)])
    for sec in split_sections(text):
        if not sec.body or sec.title.lower() == "contents":
            continue                    # a parent heading only, or the contents listing
        if sec.title == "(front matter)" or (sec.annex and not include_annexes):
            continue                    # the cover page, or an annex when not wanted
        prefix = _prefix(sec)
        whole = f"{prefix}{sec.heading}\n\n{sec.body}".strip() if sec.heading else sec.body
        own = defines_of(sec.title) or sec.parent_defines
        if len(whole) <= max_chars:
            parts = [(whole, sec.body, own)]
        else:
            room = max_chars - len(prefix) - len(sec.heading) - 20
            bodies = _pieces(sec.body, room, overlap, known)
            n = len(bodies)
            parts = [(f"{prefix}{sec.heading} (part {i}/{n})\n\n{b}".strip() if sec.heading
                      else b, b, [name] if name else own)
                     for i, (b, name) in enumerate(bodies, 1)]
        kept.append((sec, parts))

    # Mentions need the whole spec's definitions first; only the body is searched (the
    # parents' titles in the prefix are context, not citations).
    pattern = _mention_pattern({name for _, parts in kept for *_, names in parts
                                for name in names})
    segments: List[Dict[str, Any]] = []
    for sec, parts in kept:
        for i, (part, body, own) in enumerate(parts, 1):
            segments.append({**base, "text": part, "clause": sec.clause,
                             "clause_ancestors": clause_ancestors(sec.clause),
                             "title": sec.title,
                             "path": " › ".join([*sec.parents, sec.title]),
                             "defines": list(own), "mentions": mentions_in(body, pattern, own),
                             "annex": sec.annex, "part": i, "parts": len(parts),
                             "chunking": CHUNKING})
    return segments


# --- Generic markdown documents: cut along the document's own heading tree ----------

# A piece shorter than this is merged into a neighbour under the same parent.
TREE_MIN_CHARS = 300
TREE_CHUNKING = "markdown-tree-v1"


@dataclass
class _Node:
    sec: Section
    children: List["_Node"] = field(default_factory=list)

    def own(self) -> str:
        return f"{self.sec.heading}\n\n{self.sec.body}".strip() if self.sec.heading else self.sec.body

    def whole(self) -> str:
        return "\n\n".join(t for t in [self.own(), *(c.whole() for c in self.children)] if t)


def _tree(sections: List[Section]) -> List[_Node]:
    """The heading tree, rebuilt with split_sections' own parent rule."""
    roots: List[_Node] = []
    stack: List[_Node] = []
    for sec in sections:
        node = _Node(sec)
        while stack and not _is_parent(stack[-1].sec, sec.clause, sec.level):
            stack.pop()
        (stack[-1].children if stack else roots).append(node)
        stack.append(node)
    return roots


def _common(trails: List[List[str]]) -> List[str]:
    """The longest heading path shared by every trail."""
    out: List[str] = []
    for level in zip(*trails):
        if len(set(level)) != 1:
            break
        out.append(level[0])
    return out


def _merge_small(pieces: List[Dict[str, Any]], min_chars: int) -> List[Dict[str, Any]]:
    """Merge every piece shorter than min_chars into a neighbour — a sibling (same
    parent) when one is adjacent, else the smaller adjacent piece. A merged piece is
    filed under the heading path its parts share, never under one of them."""
    out = list(pieces)
    i = 0
    while len(out) > 1 and i < len(out):
        if len(out[i]["body"]) >= min_chars:
            i += 1
            continue
        near = [k for k in (i - 1, i + 1) if 0 <= k < len(out)]
        parent = out[i]["trails"][0][:-1]
        siblings = [k for k in near if any(t[:-1] == parent for t in out[k]["trails"])]
        j = min(siblings or near, key=lambda k: len(out[k]["body"]))
        a, b = sorted((i, j))
        out[a] = {"body": f"{out[a]['body']}\n\n{out[b]['body']}",
                  "trails": out[a]["trails"] + out[b]["trails"], "part": None}
        del out[b]
        i = a
    return out


def _cut(node: _Node, path: List[str], max_chars: int, min_chars: int,
         overlap: int) -> List[Dict[str, Any]]:
    """Pieces of one section: whole if it fits, else its own text and its children
    (each cut the same way), the small ones merged with a sibling; a section with no
    children that is still too long is divided by paragraph, marked (part i/n).
    Each piece keeps its heading trail (path + title)."""
    title = node.sec.title
    here = path + [title] if node.sec.heading else path
    # room left once the chunk starts with its heading path and "(part i/n)"
    room = max_chars - len(" › ".join(here)) - 20
    if len(node.whole()) <= room:
        return [{"body": node.whole(), "trails": [here], "part": None}]
    if not node.children:
        bodies = [b for b, _ in _pieces(node.sec.body, room - len(node.sec.heading) - 20,
                                        overlap)]
        n = len(bodies)
        return [{"body": f"{node.sec.heading} (part {i}/{n})\n\n{b}".strip(),
                 "trails": [here], "part": (i, n)}
                for i, b in enumerate(bodies, 1)]
    pieces: List[Dict[str, Any]] = []
    if node.sec.body:                  # a heading with no text is already in the path
        intro = _Node(Section(clause=node.sec.clause, title=title, heading=node.sec.heading,
                              body=node.sec.body, level=node.sec.level))
        pieces += _cut(intro, path, max_chars, min_chars, overlap)
    for child in node.children:
        pieces += _cut(child, here, max_chars, min_chars, overlap)
    return _merge_small(pieces, min_chars)


def chunk_markdown_tree(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    max_chars: int = SECTION_MAX_CHARS,
    min_chars: int = TREE_MIN_CHARS,
    overlap: int = SECTION_OVERLAP,
) -> List[Dict[str, Any]]:
    """Cut any markdown document along its own heading tree, whatever level its units
    sit at (OpenAPI 3.0.1 puts an object at "####", 3.2.0 at "###"):

      - a section that fits in max_chars (with everything under it) is one chunk;
      - one that does not is cut at its child headings, each child cut the same way;
      - a piece shorter than min_chars joins a neighbour under the same parent, never
        standing alone;
      - a section with no children still too long is divided by paragraph,
        "(part i/n)";
      - every chunk starts with its heading path ("... › Parameter Object › Fixed
        Fields (part 2/4)") and carries title / path / part / parts.

    Headings inside fenced code blocks ("# a PNG image as a binary file:") are not
    headings. Segments are ready for index_segments(..., chunk_size=None)."""
    base = dict(metadata or {})
    pieces: List[Dict[str, Any]] = []
    for root in _tree(split_sections(text, skip_code_fences=True)):
        pieces += _cut(root, [], max_chars, min_chars, overlap)
    pieces = _merge_small(pieces, min_chars)
    segments: List[Dict[str, Any]] = []
    for p in pieces:
        trails = [t for k, t in enumerate(p["trails"]) if t not in p["trails"][:k]]
        # a parent's own text merged with its child is filed under the child
        trails = [t for t in trails
                  if not any(len(u) > len(t) and u[:len(t)] == t for u in trails)]
        common = _common(trails) if len(trails) > 1 else trails[0][:-1]
        title = " + ".join(" › ".join(t[len(common):]) or t[-1] for t in trails)
        path = " › ".join([*common, title])
        part, parts = p["part"] or (1, 1)
        suffix = f" (part {part}/{parts})" if parts > 1 else ""
        segments.append({**base, "text": f"{path}{suffix}\n\n{p['body']}",
                         "title": title, "path": path, "part": part, "parts": parts,
                         "chunking": TREE_CHUNKING})
    return segments
