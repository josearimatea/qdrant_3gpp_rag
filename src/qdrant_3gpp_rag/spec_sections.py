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
    part repeats the section's heading and parents, marked "(part i/n)";
  - NO ANNEX: everything from the first "Annex X" heading on is left out. A 3GPP
    spec's annexes hold its finished OpenAPI / ASN.1 documents and its change
    history; indexed, they let a search "read the spec" and find the very document
    being generated or judged. The cover page (the text before the first heading)
    is left out too;
  - each chunk carries its clause, title and parent path, so a consumer can filter
    or group by clause, plus:
      clause_ancestors — the clause and all its prefixes ("12", "12.3", ...), so an
                         exact match on "12.3" selects the clause and its subtree;
      defines          — what the section defines, read from a 3GPP-style title
                         ("Type NotifyThresholdCrossing", "Enumeration: DataInd",
                         'Resource "/notificationSink"'), the clean name only;
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
CHUNKING = "sections-v2"           # recorded on every chunk: how it was cut
# Upper bound of a chunk's `mentions` list (first occurrences kept).
MAX_MENTIONS = 30

_ATX = re.compile(r"^(#{1,9})[ \t]+(?P<title>[^\n]*?)[ \t#]*$", re.MULTILINE)
_SETEXT = re.compile(r"^(?P<title>[^\n|+]*\S[^\n|+]*)\n(?P<rule>=+|-{3,})[ \t]*$", re.MULTILINE)
_ATTRS = re.compile(r"\s*\{#[^}]*\}\s*$")
_CLAUSE = re.compile(
    r"^(?:Annex\s+)?(?P<clause>\d+[a-z]?(?:\.\d+[a-z]?)*|[A-Z](?:\.\d+[a-z]?)+|[A-Z]\b)")
_ANNEX = re.compile(r"^Annex\s+[A-Z]\b", re.IGNORECASE)
_YAML_TYPE_DEF = re.compile(r"^[A-Z][A-Za-z0-9]+:\s*$", re.MULTILINE)
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
    if not rest:
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


def _headings(text: str) -> List[tuple]:
    """(start, end, level, title) of every heading, in document order."""
    found = [(m.start(), m.end(), len(m.group(1)), m.group("title")) for m in _ATX.finditer(text)]
    for m in _SETEXT.finditer(text):
        line = m.group("title").strip()
        if line.startswith(("#", ">", "-", "*", "|")) or not re.search(r"[A-Za-z0-9]", line):
            continue                                   # not a heading: a list, quote, rule
        found.append((m.start(), m.end(), 1 if m.group("rule")[0] == "=" else 2, line))
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


def split_sections(text: str) -> List[Section]:
    """Every section of a spec, in order, each knowing its parents and whether it is
    in an annex. The text before the first heading is a section titled
    '(front matter)'."""
    heads = _headings(text)
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
        sec = Section(clause=clause, title=title, heading=_ATTRS.sub("", text[start:end]).strip(),
                      body=text[end:body_end].strip(), level=level, annex=in_annex,
                      parents=[s.title for s in stack])
        sections.append(sec)
        stack.append(sec)
    return sections


def _prefix(sec: Section) -> str:
    near = sec.parents[-PARENTS_IN_TEXT:]
    return (" › ".join(near) + "\n\n") if near else ""


def _pieces(body: str, room: int, overlap: int) -> List[str]:
    """A long body divided: one YAML type definition per piece when it holds several,
    then by paragraph; never larger than `room`."""
    starts = [m.start() for m in _YAML_TYPE_DEF.finditer(body)]
    units = ([body[a:b].strip() for a, b in zip([0] + starts[1:], starts[1:] + [len(body)])]
             if len(starts) >= 2 else [body])
    splitter = RecursiveCharacterTextSplitter(chunk_size=max(room, 200), chunk_overlap=overlap,
                                              separators=["\n\n", "\n", " ", ""])
    out: List[str] = []
    for unit in units:
        out.extend([unit] if len(unit) <= room else splitter.split_text(unit))
    return [p for p in out if p.strip()]


def chunk_spec_sections(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    max_chars: int = SECTION_MAX_CHARS,
    overlap: int = SECTION_OVERLAP,
    include_annexes: bool = False,
) -> List[Dict[str, Any]]:
    """Segments ready for index_segments(..., chunk_size=None): one per section (or
    per part of a long one), text under "text", plus the given metadata and
    clause / clause_ancestors / title / path / defines / mentions / annex / part /
    parts / chunking. Annexes and the cover page are left out unless
    include_annexes=True."""
    base = dict(metadata or {})
    kept: List[tuple] = []                       # (section, its own names, [(text, body)])
    for sec in split_sections(text):
        if not sec.body or sec.title.lower() == "contents":
            continue                    # a parent heading only, or the contents listing
        if not include_annexes and (sec.annex or sec.title == "(front matter)"):
            continue                    # an annex, or the cover page
        prefix = _prefix(sec)
        whole = f"{prefix}{sec.heading}\n\n{sec.body}".strip() if sec.heading else sec.body
        if len(whole) <= max_chars:
            parts = [(whole, sec.body)]
        else:
            room = max_chars - len(prefix) - len(sec.heading) - 20
            bodies = _pieces(sec.body, room, overlap)
            n = len(bodies)
            parts = [(f"{prefix}{sec.heading} (part {i}/{n})\n\n{b}".strip() if sec.heading
                      else b, b) for i, b in enumerate(bodies, 1)]
        kept.append((sec, defines_of(sec.title), parts))

    # Mentions need the whole spec's definitions first; only the body is searched (the
    # parents' titles in the prefix are context, not citations).
    pattern = _mention_pattern({name for _, own, _ in kept for name in own})
    segments: List[Dict[str, Any]] = []
    for sec, own, parts in kept:
        for i, (part, body) in enumerate(parts, 1):
            segments.append({**base, "text": part, "clause": sec.clause,
                             "clause_ancestors": clause_ancestors(sec.clause),
                             "title": sec.title,
                             "path": " › ".join([*sec.parents, sec.title]),
                             "defines": list(own), "mentions": mentions_in(body, pattern, own),
                             "annex": sec.annex, "part": i, "parts": len(parts),
                             "chunking": CHUNKING})
    return segments
