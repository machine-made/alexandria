#!/usr/bin/env python3
"""Shared Alexandria metadata and file-identity helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import Any

VALID_CATEGORIES = {"propulsion", "quant", "optimization", "papers", "misc", "defense"}
UNKNOWN_AUTHOR = "unknown"
NODATE_YEAR = "nodate"
SOURCE_EXTENSIONS = ("pdf", "PDF", "epub", "EPUB")
SOURCE_SUFFIXES = {".pdf", ".epub"}
IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)([^)]*\))")
HTML_SRC_RE = re.compile(r"(\bsrc=[\"'])([^\"']+)([\"'])")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"(?:arxiv:\s*|arxiv\.org/abs/)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
ISBN_RE = re.compile(r"\bISBN(?:-1[03])?\s*:?\s*([0-9Xx][0-9Xx\-\s]{8,24}[0-9Xx])")
IDENTIFIER_STOP_SECTIONS = {
    "abstract",
    "introduction",
    "references",
    "reference",
    "bibliography",
    "works cited",
    "literature cited",
}
SURNAME_PARTICLES = {
    "da",
    "das",
    "de",
    "del",
    "della",
    "der",
    "di",
    "dos",
    "du",
    "la",
    "le",
    "van",
    "von",
}
AUTHOR_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
CORPORATE_AUTHOR_KEYWORDS = {
    "administration",
    "agency",
    "association",
    "center",
    "centre",
    "committee",
    "company",
    "consortium",
    "corp",
    "corporation",
    "department",
    "foundation",
    "group",
    "inc",
    "institute",
    "laboratories",
    "laboratory",
    "labs",
    "llc",
    "ltd",
    "openai",
    "press",
    "society",
    "team",
    "university",
}
ET_AL_RE = re.compile(r"(?:\s*,?\s*et\.?\s+al\.?)\s*$", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def same_text(left: str, right: str) -> bool:
    return left.casefold() in {right.casefold()}


def is_unknown_author_value(author: str) -> bool:
    return same_text(author.strip(), UNKNOWN_AUTHOR)


def is_nodate_year_value(year: str) -> bool:
    return same_text(year.strip(), NODATE_YEAR)


def is_plausible_publication_year(year: str) -> bool:
    """True when ``year`` is a filing-safe publication year.

    Bounds are inclusive: ``[1450, datetime.now().year + 1]``. Anything outside
    (table values, future typos, OCR garbage) must never become the filing year.
    """
    text = str(year or "").strip()
    if not text or is_nodate_year_value(text):
        return False
    try:
        value = int(text)
    except ValueError:
        return False
    return 1450 <= value <= datetime.now().year + 1


def year_from_doi(doi: str) -> str:
    """Extract a plausible 19xx/20xx year embedded in a DOI path segment.

    Skips the ``10.<registrant>/`` prefix so registrant numbers are not mistaken
    for years. Only path-delimited tokens (``.2026.``, ``/2020/``) are considered.

    arXiv-style DOIs (``10.48550/arXiv.YYMM.NNNNN`` or any path with an ``arxiv``
    segment) never yield a year: the YYMM token is a submission id, not a
    publication year. DOIs with multiple distinct year-like tokens are also
    skipped as ambiguous.
    """
    normalized = normalize_doi(_metadata_text(doi))
    if not normalized:
        return ""
    rest = normalized
    prefix = re.match(r"^10\.\d+/(.+)$", normalized)
    if prefix:
        rest = prefix.group(1)
    # arXiv DOIs encode submission YYMM, not publication year.
    if re.search(r"(?:^|[./_\-])arxiv(?:[./_\-]|$)", rest, re.IGNORECASE):
        return ""
    candidates: list[str] = []
    for match in re.finditer(r"(?:^|[./_\-])((?:19|20)\d{2})(?=[./_\-]|$)", rest):
        candidate = match.group(1)
        if is_plausible_publication_year(candidate) and candidate not in candidates:
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    return ""


def year_from_dates(dates: Any) -> str:
    """Derive a publication year from a pdf_meta-style dates mapping."""
    if not isinstance(dates, dict) or not dates:
        return ""
    preferred_keys = (
        "published",
        "publication",
        "date",
        "created",
        "metadata",
        "modified",
    )
    for key in preferred_keys:
        if key not in dates:
            continue
        year = _year_from_value(dates[key])
        if year and is_plausible_publication_year(year):
            return year
    for value in dates.values():
        year = _year_from_value(value)
        if year and is_plausible_publication_year(year):
            return year
    return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_paths(drop_dir: Path | None, stem: str | None = None) -> list[Path]:
    if drop_dir is None or not drop_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in drop_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if stem is not None and path.stem != stem:
            continue
        paths.append(path)
    return sorted(paths, key=lambda path: path.name.lower())


def source_files_for_stem(drop_dir: Path | None, stem: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for src in iter_source_paths(drop_dir, stem):
        rows.append(
            {
                "name": src.name,
                "format": src.suffix.lower().lstrip("."),
                "size": src.stat().st_size,
                "sha256": sha256_file(src),
            }
        )
    rows.sort(key=lambda row: row["name"].lower())
    return rows


def source_sha256_map(source_files: list[dict[str, Any]]) -> dict[str, str]:
    return {str(row["name"]): str(row["sha256"]) for row in source_files if row.get("sha256")}


def source_file_rows(data: object) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return fallback


def field(text: str, name: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def front_matter_lines(text: str, limit: int = 180) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines()[:limit]:
        line = re.sub(r"<[^>]+>", "", raw).strip()
        if not line or line.startswith("![](") or line.startswith("|"):
            continue
        lines.append(line.strip("*_ "))
    return lines


def _line_has_identifier_shape(line: str) -> bool:
    """True if *line* contains a DOI / arXiv / ISBN shape anywhere (not only as a prefix).

    Prefix labels (`DOI:`, `ISBN`, `arXiv:`) remain admitted for regression with
    incomplete values; URL and mid-line forms match via the extract regexes.
    """
    if DOI_RE.search(line) or ARXIV_RE.search(line) or ISBN_RE.search(line):
        return True
    return bool(re.match(r"^(?:doi|isbn(?:-1[03])?|arxiv)\b\s*:?", line, re.IGNORECASE))


def identifier_lines(text: str) -> str:
    rows = []
    for line in front_matter_lines(text, 220):
        normalized = re.sub(r"^#{1,6}\s*", "", line).strip(" *_:").lower()
        if normalized in IDENTIFIER_STOP_SECTIONS:
            break
        if _line_has_identifier_shape(line):
            rows.append(line)
    return "\n".join(rows)


def looks_like_author(line: str) -> bool:
    if not (3 <= len(line) <= 100):
        return False
    lowered = line.lower()
    blocked = ("chapter", "contents", "features", "press", "copyright", "isbn", "doi", "www.", "http")
    if any(word in lowered for word in blocked):
        return False
    if re.search(r"\d", line):
        return False
    words = [word for word in re.split(r"\s+", line.replace(".", "")) if word]
    return len(words) >= 2 and all(re.match(r"^[A-Z][A-Za-z\-']*$", word) for word in words[-2:])


# Interior function words that mark a title/subtitle fragment, not a person name.
# Surname particles (van/de/der/…) are exempt — see is_plausible_person_author.
_AUTHOR_FUNCTION_WORDS = frozenset({"in", "of", "and", "for", "the", "a", "an"})
_ROLE_SERIES_AUTHOR_RE = re.compile(
    r"(?ix)"
    r"^(?:"
    r"series\s+editors?|editors?|editorial\s+board|"
    r"(?:series|volume|associate|managing|guest)\s+\w+"
    r")$"
    r"|^(?:a|an|the)\s+.+\s+"
    r"(?:perspective|survey|introduction|overview|handbook|review|primer)\s*$"
)
_TITLE_OVERLAP_MIN_CHARS = 8
_TITLE_TOKEN_OVERLAP = 0.6
_MULTI_AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+|;\s*", re.IGNORECASE)


def _ws_tokens(text: str) -> list[str]:
    return [tok for tok in re.split(r"\s+", text.replace(",", " ").strip()) if tok]


def _token_set(text: str) -> set[str]:
    return {m.group(0).casefold() for m in re.finditer(r"[A-Za-z0-9']+", text)}


def _is_name_initial(tok: str) -> bool:
    return bool(re.match(r"^[A-Z]\.?$", tok.strip()))


def author_title_overlap(author: str, title: str) -> bool:
    """True when author equals, substrings, or heavily token-overlaps the title."""
    a = str(author or "").strip()
    t = str(title or "").strip()
    if not a or not t:
        return False
    if same_text(a, t):
        return True
    a_cf, t_cf = a.casefold(), t.casefold()
    if len(a_cf) >= _TITLE_OVERLAP_MIN_CHARS and (a_cf in t_cf or t_cf in a_cf):
        return True
    ta, tb = _token_set(a), _token_set(t)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / min(len(ta), len(tb))
    return overlap >= _TITLE_TOKEN_OVERLAP


def is_role_or_series_author(author: str) -> bool:
    """True for role/series labels (Series Editors, A … Perspective/Survey, …)."""
    text = str(author or "").strip()
    if not text:
        return False
    if _ROLE_SERIES_AUTHOR_RE.search(text):
        return True
    lowered = text.casefold()
    if re.search(r"\beditors?\b", lowered):
        return True
    return False


# Whole-value template/placeholder strings (PDF form fields, SSRN stubs, …).
# Match is case-insensitive on the full stripped value only — never substring —
# so a real author like "Nameet Kaur" still files.
_PLACEHOLDER_METADATA_VALUES = frozenset(
    {
        "your name",
        "your title",
        "author name",
        "author",
        "first last",
        "firstname lastname",
        "john doe",
        "jane doe",
        "name",
        "title",
        "untitled",
        "unknown",
        "n/a",
        "tbd",
        "todo",
    }
)


def is_placeholder_metadata_value(value: str) -> bool:
    """True when ``value`` is a whole-value template/placeholder string."""
    text = str(value or "").strip().casefold()
    return bool(text) and text in _PLACEHOLDER_METADATA_VALUES


def _is_single_person_name(text: str, *, strict: bool = False) -> bool:
    """Person-shape for one name (no multi-author separators).

    ``strict`` is used for each side of an ``and``/``;`` list: requires a
    compact Given + Surname shape (2 tokens, or 3 with initial/particle) so
    title fragments like ``Pulse Detonation and Rotating Detonation Engines``
    cannot pass as a two-author list.
    """
    text = str(text or "").strip()
    if not text or is_unknown_author_value(text):
        return False
    if re.match(r"^(?:A|An|The)\s+", text):
        return False
    tokens = _ws_tokens(text)
    if not tokens or len(tokens) > 4:
        return False
    # Strict list segments keep Given+Surname compactness so title fragments
    # split on "and"/";" cannot pass as multi-author. Non-strict allows a lone
    # surname ("Kobelev") or particle+surname ("van der Berg") with no given.
    if strict and len(tokens) < 2:
        return False

    has_particle = False
    has_initial = False
    nameish = 0
    for tok in tokens:
        key = re.sub(r"[^A-Za-z]", "", tok).casefold()
        if not key:
            return False
        # Initials before function-word check so "A." is not treated as article "a".
        if _is_name_initial(tok):
            has_initial = True
            nameish += 1
            continue
        if key in SURNAME_PARTICLES:
            has_particle = True
            continue
        if key in _AUTHOR_FUNCTION_WORDS:
            return False
        bare = tok.strip(".")
        if tok[:1].islower():
            return False
        if re.match(r"^[A-Z][A-Za-z\-']*$", bare):
            nameish += 1
            continue
        return False
    # strict: Given+Surname (nameish >= 2). non-strict: one nameish token is a
    # surname; particle forms reuse has_particle (nameish can be 1).
    min_nameish = 2 if strict else 1
    if nameish < min_nameish:
        return False
    if len(tokens) == 4 and not has_particle:
        return False
    if strict and len(tokens) == 3 and not has_particle and not has_initial:
        return False
    if strict and len(tokens) > 3:
        return False
    return True


def is_plausible_person_author(author: str, title: str = "") -> bool:
    """True when ``author`` looks like a real person name safe to file under.

    Rejects title-fragments, role/series labels, template placeholders, and
    non-person shapes. Surname particles (van, de, der, …) and multi-author
    lists (``A and B`` / ``A; B``) are allowed when each segment is
    person-shaped. Empty/unknown → False. A placeholder author *or* title is
    not filable.
    """
    text = str(author or "").strip()
    if not text or is_unknown_author_value(text):
        return False
    if is_placeholder_metadata_value(text):
        return False
    if title and is_placeholder_metadata_value(str(title)):
        return False
    if _looks_like_corporate_author(text):
        return False
    if is_role_or_series_author(text):
        return False
    if title and author_title_overlap(text, str(title)):
        return False

    if _MULTI_AUTHOR_SPLIT_RE.search(text):
        parts = [p.strip() for p in _MULTI_AUTHOR_SPLIT_RE.split(text) if p.strip()]
        if len(parts) < 2:
            return False
        return all(_is_single_person_name(part, strict=True) for part in parts)
    return _is_single_person_name(text, strict=False)


def inferred_author(text: str) -> str:
    lines = front_matter_lines(text)
    for idx, line in enumerate(lines):
        if re.match(r"^#\s+", line):
            for candidate in lines[idx + 1 : idx + 8]:
                candidate = re.sub(r"^#+\s*", "", candidate).strip("*_ ")
                if looks_like_author(candidate):
                    return candidate
    return ""


def inferred_year(text: str) -> str:
    """Heuristic year from markdown front-matter.

    Explicit year-context patterns (copyright / published / first edition) win
    over a bare 19xx/20xx token. Bare tokens are demoted: only a *plausible*
    year is returned, so table values like heat capacity ``2029 J/kg/K`` cannot
    become the filing year when they fall outside the publication-year bounds.
    """
    front = "\n".join(text.splitlines()[:220])
    patterns = (
        r"(?:©|copyright)\s*(?:\D{0,30})((?:19|20)\d{2})",
        r"first edition published\s+((?:19|20)\d{2})",
        r"published\s+((?:19|20)\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, front, re.IGNORECASE)
        if match and is_plausible_publication_year(match.group(1)):
            return match.group(1)
    for year in re.findall(r"\b((?:19|20)\d{2})\b", front):
        if is_plausible_publication_year(year):
            return year
    return ""


# Topical category keywords. Matching is always word-bounded so morphological
# false positives (quant ⊂ quantify/quantitative; bare substring hits) cannot
# steal a domain shelf. Order is precedence among topical domains.
_CATEGORY_KEYWORD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("propulsion", ("rocket", "propulsion", "aerospace", "satellite launch")),
    ("quant", ("portfolio", "option pricing", "financial data science", "quant", "quantitative finance")),
    ("optimization", ("optimization", "optimisation")),
)


def _word_bounded_term(sample: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", sample) is not None


def _heading_sample(text: str, limit: int = 120) -> str:
    """Lowercased markdown headings from the front-matter window."""
    headings: list[str] = []
    for line in front_matter_lines(text, limit):
        if re.match(r"^#+\s+", line):
            headings.append(re.sub(r"^#+\s*", "", line).strip())
    return "\n".join(headings).lower()


def _keyword_category(sample: str, *, headings: str = "") -> str:
    for category, terms in _CATEGORY_KEYWORD_GROUPS:
        # "optimization"/"optimisation" appear casually in CFD abstracts
        # ("optimization problem for the mesh"). Require a heading/title hit for
        # that group so body-only mentions do not steal the optimization shelf;
        # propulsion/quant still scan the full front-matter sample.
        haystack = headings if category == "optimization" else sample
        if not haystack:
            continue
        if any(_word_bounded_term(haystack, term) for term in terms):
            return category
    return ""


def inferred_category(text: str) -> str:
    """Return a topical category from word-bounded front-matter keywords, or ""."""
    sample = "\n".join(front_matter_lines(text, 120)).lower()
    return _keyword_category(sample, headings=_heading_sample(text))


def _article_signal(metadata: dict[str, Any]) -> bool:
    """True when local evidence marks the work as a journal/preprint article."""
    if _metadata_text(metadata.get("doi")):
        return True
    if _metadata_text(metadata.get("arxiv_id")):
        return True
    if _metadata_text(metadata.get("journal")):
        return True
    return False


def _apply_category_decision(metadata: dict[str, Any]) -> None:
    """Fill category from evidence and record decision-path provenance.

    Precedence (after higher ladder tiers have already won field-wise):
      1. topical keyword → domain, source ``keyword``
      2. else DOI / arXiv / non-empty journal → ``papers``, source ``doi_default``
      3. else → ``misc``, source ``heuristic``

    Explicit markdown/manual/resolved category values keep their ladder source
    (e.g. ``manual``). Heuristic keyword hits are relabeled ``keyword`` so the
    decision path is auditable — not a flat ``heuristic``.
    """
    sources = metadata.setdefault("metadata_field_sources", {})
    if not isinstance(sources, dict):
        sources = {}
        metadata["metadata_field_sources"] = sources

    category = _valid_category(metadata.get("category"))
    source = str(sources.get("category") or "")

    if category and source in {"resolved", "pdf", "epub", "markdown", "manual", "keyword", "doi_default"}:
        # Explicit ladder tier, or a prior decision-path label — keep as-is.
        metadata["category"] = category
        return

    if category and source == "heuristic" and category != "misc":
        # Heuristic layer only supplies a non-misc category on a keyword hit.
        metadata["category"] = category
        sources["category"] = "keyword"
        return

    # No topical category yet — article signal → papers, else misc.
    if _article_signal(metadata):
        metadata["category"] = "papers"
        sources["category"] = "doi_default"
        return

    metadata["category"] = "misc"
    sources["category"] = "heuristic"


def _apply_year_decision(metadata: dict[str, Any]) -> None:
    """Resolve year from strongest evidence and enforce the plausibility gate.

    Year evidence precedence (strongest first), after the field-wise ladder has
    already merged tiers:
      1. resolved ladder year (keep source ``resolved``)
      2. DOI-embedded year → source ``doi``
         (if a pdf dates-year also exists and |doi − dates| > 1, prefer dates)
      3. existing pdf/epub/markdown ladder year (pdf may be dates-derived)
      4. heuristic bare-token / context year (only if still plausible)
      5. residual ``dates`` map on the merged metadata → source ``pdf``

    A candidate outside ``[1450, now.year+1]`` is never filed: the year becomes
    ``nodate`` so ``metadata_confidence`` holds low rather than filing a future
    or table-value year at high confidence.
    """
    sources = metadata.setdefault("metadata_field_sources", {})
    if not isinstance(sources, dict):
        sources = {}
        metadata["metadata_field_sources"] = sources

    year = str(metadata.get("year") or "").strip()
    source = str(sources.get("year") or "")

    if year and not is_nodate_year_value(year) and not is_plausible_publication_year(year):
        year = ""
        source = ""
        metadata.pop("year", None)
        sources.pop("year", None)

    if source == "resolved" and year and is_plausible_publication_year(year):
        metadata["year"] = year
        sources["year"] = "resolved"
        return

    dates = metadata.get("dates")
    dates_year = year_from_dates(dates) if isinstance(dates, dict) else ""
    # Existing pdf/epub year is often dates-derived (see pdf_meta_layer).
    if not dates_year and year and is_plausible_publication_year(year) and source in {"pdf", "epub"}:
        dates_year = year

    doi_year = year_from_doi(_metadata_text(metadata.get("doi")))
    if doi_year:
        if dates_year and abs(int(doi_year) - int(dates_year)) > 1:
            # DOI string parsing is less reliable than pdf dates when they conflict.
            metadata["year"] = dates_year
            if source in {"pdf", "epub"} and year == dates_year:
                sources["year"] = source
            else:
                dates_source = str(sources.get("dates") or "")
                sources["year"] = (
                    dates_source if dates_source in {"pdf", "epub", "resolved"} else "pdf"
                )
            return
        metadata["year"] = doi_year
        sources["year"] = "doi"
        return

    if year and is_plausible_publication_year(year):
        metadata["year"] = year
        if source:
            sources["year"] = source
        return

    if dates_year:
        metadata["year"] = dates_year
        # dates almost always arrive via the pdf tier; fall back carefully.
        dates_source = str(sources.get("dates") or "")
        sources["year"] = dates_source if dates_source in {"pdf", "epub", "resolved"} else "pdf"
        return

    metadata["year"] = NODATE_YEAR
    sources["year"] = source or "heuristic"


# Crossref Funder Registry registrant — never a scholarly work DOI.
FUNDER_DOI_PREFIX = "10.13039/"


def normalize_doi(raw: str) -> str:
    doi = raw.strip()
    # Strip trailing prose punctuation until stable: removing an unbalanced
    # paren can re-expose a sentence period ("(doi: 10.1234/x.y.)"), so a single
    # pass in a fixed order is not enough.
    while doi:
        if doi[-1] in ".,;:":
            doi = doi[:-1]
            continue
        # DOI_RE's charclass includes (), so a closing paren from surrounding
        # prose is greedily eaten. Strip a trailing unbalanced ')' only; balanced
        # paths like Lancet/Elsevier ``10.1016/S0140-6736(00)02034-9`` stay intact.
        if doi.endswith(")") and doi.count(")") > doi.count("("):
            doi = doi[:-1]
            continue
        break
    return doi.lower()


def _is_funder_doi(doi: str) -> bool:
    return bool(doi) and doi.startswith(FUNDER_DOI_PREFIX)


def extract_doi(text: str) -> str:
    """First non-funder DOI match in *text* (positional).

    Funder-registry DOIs (``10.13039/``) are never returned as a work DOI; the
    next non-funder match wins. Still first-match otherwise — no labeled-line
    preference (that lives in :func:`extract_doi_from_identifier_lines`).

    ``alexandria_pdf_meta`` does NOT call this — it carries its own copy of
    ``DOI_RE``/``extract_doi``/``normalize_doi``, so fixes here do not reach the
    PDF path. Keep the two copies in lockstep (see ``test_identifier_parity.sh``).
    """
    for match in DOI_RE.finditer(text):
        doi = normalize_doi(match.group(1))
        if doi and not _is_funder_doi(doi):
            return doi
    return ""


def extract_doi_from_identifier_lines(identifiers: str) -> str:
    """Select a work DOI from admitted markdown identifier lines.

    When more than one distinct DOI appears, a **line-initial labeled** form
    (``^doi\\s*:?\\s*<doi>`` — the old line-prefix predicate) wins over
    incidental prose/URL hits. With no labeled line, fall back to first-match
    over the admitted bag (recovers Marker mid-prose own-DOIs such as song2023).

    Funder-registry DOIs (``10.13039/``) are never returned. This helper is for
    the markdown path only (labeled-line preference). :func:`extract_doi` also
    skips funders but stays positional first-match otherwise.
    """
    if not identifiers:
        return ""

    for line in identifiers.splitlines():
        stripped = line.strip()
        label = re.match(r"^doi\s*:?\s*", stripped, re.IGNORECASE)
        if not label:
            continue
        match = DOI_RE.search(stripped[label.end() :])
        if not match:
            continue
        doi = normalize_doi(match.group(1))
        if doi and not _is_funder_doi(doi):
            return doi

    for match in DOI_RE.finditer(identifiers):
        doi = normalize_doi(match.group(1))
        if doi and not _is_funder_doi(doi):
            return doi
    return ""


def normalize_isbn(raw: str) -> str:
    isbn = re.sub(r"[^0-9Xx]", "", raw).upper()
    return isbn if len(isbn) in {10, 13} else ""


def extract_isbn(text: str) -> str:
    for match in ISBN_RE.finditer(text):
        isbn = normalize_isbn(match.group(1))
        if isbn:
            return isbn
    return ""


def extract_arxiv_id(text: str) -> str:
    match = ARXIV_RE.search(text)
    return match.group(1) if match else ""


PDF_META_FILENAME = "pdf_meta.json"
EPUB_META_FILENAME = "epub_meta.json"
PRECEDENCE_LADDER = (
    "resolved DOI/arXiv/ISBN",
    "pdf_meta.json XMP/Info",
    "epub_meta.json OPF/Dublin Core",
    "parsed Markdown identifiers/front-matter",
    "heuristic fallback",
    "manual/default fallback",
)
PRECEDENCE_FIELDS = (
    "title",
    "author",
    "authors",
    "year",
    "category",
    "doi",
    "isbn",
    "arxiv_id",
    "publisher",
    "journal",
    "pages",
    "page_count",
    "dates",
)


def authors_from_author(author: str) -> list[str]:
    author = author.strip()
    if not author or is_unknown_author_value(author):
        return []
    parts = [part.strip() for part in re.split(r"\s*;\s*|\s+and\s+|\s+&\s+", author) if part.strip()]
    return parts or [author]


def _has_metadata_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_metadata_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_metadata_value(item) for item in value)
    return True


def _metadata_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        for key in ("x-default", "en", "en-US"):
            if key in value:
                text = _metadata_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = _metadata_text(item)
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [_metadata_text(item) for item in value]
        return "; ".join(part for part in parts if part)
    return re.sub(r"\s+", " ", str(value)).strip()


def _metadata_authors(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        authors: list[str] = []
        for item in value:
            authors.extend(_metadata_authors(item))
        return authors
    return authors_from_author(_metadata_text(value))


def _first_layer_value(*values: Any) -> Any:
    for value in values:
        if _has_metadata_value(value):
            return value
    return ""


def _year_from_value(value: Any) -> str:
    text = _metadata_text(value)
    match = re.search(r"\b((?:18|19|20|21)\d{2})\b", text)
    return match.group(1) if match else ""


def _valid_category(value: Any) -> str:
    category = _metadata_text(value).lower()
    return category if category in VALID_CATEGORIES else ""


def _normalize_metadata_layer(layer: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(layer, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key in PRECEDENCE_FIELDS:
        if key not in layer or not _has_metadata_value(layer[key]):
            continue
        value = layer[key]
        if key == "category":
            category = _valid_category(value)
            if category:
                normalized[key] = category
        elif key == "authors":
            authors = _metadata_authors(value)
            if authors:
                normalized[key] = authors
        elif key == "author":
            author = _metadata_text(value)
            if author and not is_unknown_author_value(author):
                normalized[key] = author
        elif key == "year":
            year = _year_from_value(value) or _metadata_text(value)
            if year and is_nodate_year_value(year):
                normalized[key] = NODATE_YEAR
            elif year and is_plausible_publication_year(year):
                normalized[key] = year
            # Implausible years (table values, far-future) never enter a layer.
        elif key == "doi":
            doi = normalize_doi(_metadata_text(value))
            if doi:
                normalized[key] = doi
        elif key == "isbn":
            isbn = normalize_isbn(_metadata_text(value)) or _metadata_text(value)
            if isbn:
                normalized[key] = isbn
        elif key in {"dates"} and isinstance(value, dict):
            dates = {str(k): v for k, v in value.items() if _has_metadata_value(v)}
            if dates:
                normalized[key] = dates
        elif key in {"pages", "page_count"} and isinstance(value, (int, float)):
            normalized[key] = value
        else:
            text = _metadata_text(value)
            normalized[key] = text if text else value
    if "authors" not in normalized and "author" in normalized:
        authors = authors_from_author(str(normalized["author"]))
        if authors:
            normalized["authors"] = authors
    if "author" not in normalized and "authors" in normalized:
        normalized["author"] = "; ".join(str(author) for author in normalized["authors"])
    return normalized


def load_pdf_meta(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    return load_json(path)


def pdf_meta_path_for_work(work: Path, stem: str) -> Path | None:
    candidates = (work / PDF_META_FILENAME, work / stem / PDF_META_FILENAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def pdf_meta_layer(pdf_meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pdf_meta, dict) or not pdf_meta:
        return {}
    nested = pdf_meta.get("normalized") if isinstance(pdf_meta.get("normalized"), dict) else {}
    layer: dict[str, Any] = {}
    for key in PRECEDENCE_FIELDS:
        value = _first_layer_value(nested.get(key), pdf_meta.get(key))
        if _has_metadata_value(value):
            layer[key] = value
    # Many PDFs carry Info/XMP dates but no discrete year field. Derive a
    # pdf-tier year from dates so the heuristic bare-token cannot win by default.
    # (DOI-embedded year is applied later in _apply_year_decision; dates win on
    # conflict of more than one year — see that function.)
    if not _has_metadata_value(layer.get("year")):
        dates = layer.get("dates")
        if not isinstance(dates, dict):
            dates = _first_layer_value(nested.get("dates"), pdf_meta.get("dates"))
        dates_year = year_from_dates(dates) if isinstance(dates, dict) else ""
        if dates_year:
            layer["year"] = dates_year
    return _normalize_metadata_layer(layer)


def epub_meta_layer(epub_meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(epub_meta, dict) or not epub_meta:
        return {}
    layer = {key: epub_meta[key] for key in PRECEDENCE_FIELDS if _has_metadata_value(epub_meta.get(key))}
    if "authors" not in layer and _has_metadata_value(epub_meta.get("creators")):
        layer["authors"] = epub_meta["creators"]
    return _normalize_metadata_layer(layer)


def markdown_metadata_layer(text: str) -> dict[str, Any]:
    identifiers = identifier_lines(text)
    return _normalize_metadata_layer(
        {
            "title": field(text, "title"),
            "author": field(text, "author"),
            "year": field(text, "year"),
            "category": field(text, "category"),
            "doi": extract_doi_from_identifier_lines(identifiers),
            "isbn": extract_isbn(identifiers),
            "arxiv_id": extract_arxiv_id(identifiers),
        }
    )


def heuristic_metadata_layer(text: str, stem: str) -> dict[str, Any]:
    # Category is keyword-only here. DOI/arXiv/journal → papers and the bare
    # misc default are applied later in _apply_category_decision so identifiers
    # from higher precedence tiers (resolved/markdown) can still route articles.
    layer: dict[str, Any] = {
        "title": first_heading(text, stem),
        "author": inferred_author(text) or UNKNOWN_AUTHOR,
        "year": inferred_year(text) or NODATE_YEAR,
    }
    category = inferred_category(text)
    if category:
        layer["category"] = category
    return _normalize_metadata_layer(layer)


def apply_metadata_precedence(
    *,
    resolved: dict[str, Any] | None = None,
    pdf: dict[str, Any] | None = None,
    epub: dict[str, Any] | None = None,
    markdown: dict[str, Any] | None = None,
    heuristic: dict[str, Any] | None = None,
    manual: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge metadata by the documented Alexandria precedence ladder.

    The order is deterministic and field-wise: resolved DOI/arXiv/ISBN data wins,
    then durable ``pdf_meta.json`` XMP/Info, then parsed Markdown identifiers and
    front-matter, then heuristic fallbacks, then manual/default fallbacks. Once a
    tier supplies a non-empty field, lower tiers are not allowed to override it.
    """
    tiers = (
        ("resolved", _normalize_metadata_layer(resolved)),
        ("pdf", _normalize_metadata_layer(pdf)),
        ("epub", _normalize_metadata_layer(epub)),
        ("markdown", _normalize_metadata_layer(markdown)),
        ("heuristic", _normalize_metadata_layer(heuristic)),
        ("manual", _normalize_metadata_layer(manual)),
    )
    metadata: dict[str, Any] = {}
    field_sources: dict[str, str] = {}
    for source, layer in tiers:
        for key in PRECEDENCE_FIELDS:
            if key in metadata and _has_metadata_value(metadata[key]):
                continue
            if key in layer and _has_metadata_value(layer[key]):
                metadata[key] = layer[key]
                field_sources[key] = source
    if "author" in metadata and "authors" not in metadata:
        authors = authors_from_author(str(metadata["author"]))
        if authors:
            metadata["authors"] = authors
            field_sources["authors"] = field_sources.get("author", "heuristic")
    if "authors" in metadata and "author" not in metadata:
        metadata["author"] = "; ".join(str(author) for author in metadata["authors"])
        field_sources["author"] = field_sources.get("authors", "heuristic")
    metadata["metadata_field_sources"] = field_sources
    _apply_category_decision(metadata)
    _apply_year_decision(metadata)
    return metadata


def metadata_confidence(metadata: dict[str, Any]) -> str:
    kind = str(metadata.get("kind") or "").strip()
    title = str(metadata.get("title") or "").strip()
    if kind == "informal":
        has_source = any(
            str(metadata.get(field) or "").strip()
            for field in ("source", "url", "source_url", "show", "origin")
        )
        if title and has_source:
            return "high"
        return "low"

    author = str(metadata.get("author") or "").strip()
    year = str(metadata.get("year") or "").strip()
    stem = str(metadata.get("source_stem") or "").strip()
    field_sources = metadata.get("metadata_field_sources")
    has_structured_id = bool(metadata.get("doi") or metadata.get("arxiv_id") or metadata.get("isbn"))
    # Format-agnostic heuristic hold (was epub-only via source_formats == {"epub"}).
    # Any format whose title OR author came from heuristic/manual holds when there
    # is no DOI/ISBN/arXiv — closes the PDF medium-file hole. Identifiers alone
    # must not rescue a non-person author: that is the author-shape gate below.
    if isinstance(field_sources, dict):
        if field_sources.get("title") in {"heuristic", "manual"} or field_sources.get("author") in {
            "heuristic",
            "manual",
        }:
            if not has_structured_id:
                return "low"
    if (
        is_unknown_author_value(author)
        or not is_author_key_resolved(author)
        or is_nodate_year_value(year)
        or (year and not is_plausible_publication_year(year))
        or not title
        or same_text(title, stem)
    ):
        return "low"
    # Author-shape gate BEFORE isbn/doi → high: title-fragments and role labels
    # must not be rescued by an identifier.
    if not is_plausible_person_author(author, title):
        return "low"
    if has_structured_id:
        return "high"
    return "medium"


def metadata_source(metadata: dict[str, Any]) -> str:
    if metadata.get("doi"):
        return "doi"
    if metadata.get("arxiv_id"):
        return "arxiv"
    if metadata.get("isbn"):
        return "isbn"
    field_sources = metadata.get("metadata_field_sources")
    if isinstance(field_sources, dict) and "pdf" in set(field_sources.values()):
        return "pdf"
    if isinstance(field_sources, dict) and "epub" in set(field_sources.values()):
        return "epub"
    return "heuristic"


def _finalize_metadata(metadata: dict[str, Any], stem: str, source_files: list[dict[str, Any]]) -> dict[str, Any]:
    metadata.setdefault("title", stem)
    metadata.setdefault("author", UNKNOWN_AUTHOR)
    if not metadata.get("authors"):
        metadata["authors"] = authors_from_author(str(metadata.get("author") or ""))
    metadata.setdefault("year", NODATE_YEAR)
    category = str(metadata.get("category") or "misc").strip().lower()
    metadata["category"] = category if category in VALID_CATEGORIES else "misc"
    for key in ("doi", "isbn", "arxiv_id"):
        metadata.setdefault(key, "")
    metadata["source_stem"] = str(metadata.get("source_stem") or stem)
    if source_files:
        metadata["source_files"] = source_files
        metadata["source_sha256"] = source_sha256_map(source_files)
    else:
        metadata.setdefault("source_files", [])
        metadata.setdefault("source_sha256", {})
    metadata.setdefault("parsed_at", utc_now())
    metadata.setdefault("parser", "marker")
    metadata["metadata_source"] = metadata_source(metadata)
    metadata["metadata_confidence"] = metadata_confidence(metadata)
    return metadata


def infer_metadata(
    text: str,
    stem: str,
    source_files: list[dict[str, Any]] | None = None,
    *,
    pdf_meta_path: Path | None = None,
    pdf_meta: dict[str, Any] | None = None,
    epub_meta: dict[str, Any] | None = None,
    resolved_metadata: dict[str, Any] | None = None,
    manual_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_files = source_files or []
    pdf_data = pdf_meta if pdf_meta is not None else load_pdf_meta(pdf_meta_path)
    metadata = apply_metadata_precedence(
        resolved=resolved_metadata,
        pdf=pdf_meta_layer(pdf_data),
        epub=epub_meta_layer(epub_meta),
        markdown=markdown_metadata_layer(text),
        heuristic=heuristic_metadata_layer(text, stem),
        manual=manual_metadata,
    )
    return _finalize_metadata(metadata, stem, source_files)


# Opt-in DOI enrichment (additive gap-fill). Off by default; never touches identity.
RESOLVE_ENRICH_ENV = "ALEXANDRIA_RESOLVE_ENRICH"
ENRICH_ALLOWLIST = ("journal", "publisher", "pages")
IDENTITY_FIELDS = frozenset(
    {
        "title",
        "author",
        "authors",
        "year",
        "doi",
        "isbn",
        "arxiv_id",
        "category",
    }
)


def resolve_enrichment_enabled(env: dict[str, str] | None = None) -> bool:
    """True only when ALEXANDRIA_RESOLVE_ENRICH is an explicit truthy value."""

    environ = os.environ if env is None else env
    value = str(environ.get(RESOLVE_ENRICH_ENV, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def enrich_metadata_from_doi(
    metadata: dict[str, Any],
    *,
    fetch: Any | None = None,
    cache_dir: Path | str | None = None,
    timeout: float | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Fill missing non-identity fields from the DOI resolver (opt-in, additive).

    When *enabled* is None, the ``ALEXANDRIA_RESOLVE_ENRICH`` env flag is read
    (off/unset → no-op, zero network). Only ``journal``/``publisher``/``pages``
    that are currently empty may be filled. Identity/key-affecting fields are
    never written, even when empty. Resolver failures are non-fatal.
    """

    if enabled is None:
        enabled = resolve_enrichment_enabled()
    if not enabled:
        return metadata

    doi = _metadata_text(metadata.get("doi"))
    if not doi:
        return metadata

    try:
        # Lazy import: resolvers already imports this module for normalize_doi/isbn.
        from alexandria_resolvers import STATUS_FOUND, default_fetch, resolve_doi

        resolve_kwargs: dict[str, Any] = {"fetch": default_fetch if fetch is None else fetch}
        if cache_dir is not None:
            resolve_kwargs["cache_dir"] = cache_dir
        if timeout is not None:
            resolve_kwargs["timeout"] = timeout
        result = resolve_doi(doi, **resolve_kwargs)

        if not isinstance(result, dict) or result.get("status") != STATUS_FOUND:
            return metadata
        resolved = result.get("metadata")
        if not isinstance(resolved, dict):
            return metadata

        sources = metadata.get("metadata_field_sources")
        if not isinstance(sources, dict):
            sources = {}
            metadata["metadata_field_sources"] = sources

        for field in ENRICH_ALLOWLIST:
            if field in IDENTITY_FIELDS:
                continue
            if _has_metadata_value(metadata.get(field)):
                continue
            raw = resolved.get(field)
            if not _has_metadata_value(raw):
                continue
            if field == "pages" and isinstance(raw, (int, float)):
                value: Any = int(raw) if float(raw) == int(raw) else raw
            else:
                text = _metadata_text(raw)
                if not text:
                    continue
                value = text
            metadata[field] = value
            sources[field] = "resolved"
    except Exception as exc:  # noqa: BLE001 - filing path must never abort on enrichment
        print(
            f"alexandria: DOI enrichment skipped: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return metadata


def _preserve_existing_field_sources(metadata: dict[str, Any], existing: dict[str, Any]) -> None:
    existing_sources = existing.get("metadata_field_sources")
    current_sources = metadata.setdefault("metadata_field_sources", {})
    if not isinstance(existing_sources, dict) or not isinstance(current_sources, dict):
        return
    for key, source in existing_sources.items():
        if key in current_sources:
            continue
        if existing.get(key) == metadata.get(key):
            current_sources[str(key)] = str(source)


def merge_metadata(
    existing: dict[str, Any],
    stem: str,
    source_files: list[dict[str, Any]],
    *,
    pdf_meta_path: Path | None = None,
    pdf_meta: dict[str, Any] | None = None,
    resolved_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pdf_data = pdf_meta if pdf_meta is not None else load_pdf_meta(pdf_meta_path)
    resolved_data = resolved_metadata if resolved_metadata is not None else existing.get("resolved_metadata")
    if pdf_data or resolved_data:
        metadata = apply_metadata_precedence(
            resolved=resolved_data if isinstance(resolved_data, dict) else None,
            pdf=pdf_meta_layer(pdf_data),
            manual=existing,
        )
        _preserve_existing_field_sources(metadata, existing)
    else:
        metadata = dict(existing)
    return _finalize_metadata(metadata, stem, source_files)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def transliterate_ascii(text: str) -> str:
    """Best-effort stdlib transliteration for citation-key text.

    NFKD decomposition turns common diacritics into base ASCII letters plus
    combining marks; stripping the marks keeps names like "José Álvarez" as
    "Jose Alvarez" before the key slugger removes punctuation/spaces.
    Characters with no ASCII decomposition are intentionally dropped so callers
    can detect unresolved non-Latin/CJK names and hold them as low-confidence.
    """
    decomposed = unicodedata.normalize("NFKD", str(text))
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return without_marks.encode("ascii", "ignore").decode("ascii")


def slug_word(text: str) -> str:
    text = transliterate_ascii(text).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text or UNKNOWN_AUTHOR


def _token_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", transliterate_ascii(text).lower())


def _name_tokens(text: str) -> list[str]:
    return [part for part in re.split(r"\s+", text.strip()) if part]


def _strip_trailing_et_al(text: str) -> str:
    text = str(text).strip()
    previous = None
    while previous != text:
        previous = text
        text = ET_AL_RE.sub("", text).strip(" ,;")
    return text


def _is_suffix_phrase(text: str) -> bool:
    tokens = [_token_key(token) for token in _name_tokens(text)]
    return bool(tokens) and all(token in AUTHOR_SUFFIXES for token in tokens)


def _strip_suffix_tokens(tokens: list[str]) -> list[str]:
    tokens = list(tokens)
    while tokens and _token_key(tokens[-1]) in AUTHOR_SUFFIXES:
        tokens.pop()
    return tokens


def _looks_like_corporate_author(author: str) -> bool:
    """Return True for organization-like author strings that should be held.

    This is deliberately conservative for filing safety: obvious organizations
    (ampersands, institutional suffixes, all-caps acronyms, known org brands)
    are treated as unresolved rather than guessed into misleading person keys.
    """
    text = _strip_trailing_et_al(author)
    if not text or is_unknown_author_value(text):
        return False
    if "&" in text:
        return True
    ascii_text = transliterate_ascii(text)
    words = [word.lower() for word in re.findall(r"[A-Za-z]+", ascii_text)]
    if any(word in CORPORATE_AUTHOR_KEYWORDS for word in words):
        return True
    acronym_tokens = []
    for token in re.findall(r"[A-Za-z][A-Za-z&.\-]*", ascii_text):
        letters = re.sub(r"[^A-Za-z]", "", token)
        if len(letters) < 2 or letters.lower() in AUTHOR_SUFFIXES:
            continue
        acronym_tokens.append(letters)
    if len(acronym_tokens) == 1 and acronym_tokens[0].isupper():
        return True
    return False


def _first_author_segment(author: str) -> str:
    text = _strip_trailing_et_al(author)
    text = re.split(r"\s*;\s*", text, maxsplit=1)[0].strip()
    text = re.split(r"\s+&\s+", text, maxsplit=1)[0].strip()
    text = re.split(r"\s+and\s+", text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return _strip_trailing_et_al(text)


def _surname_tokens_from_space_name(name: str) -> list[str]:
    tokens = _strip_suffix_tokens(_name_tokens(name))
    if not tokens:
        return []
    start = len(tokens) - 1
    while start > 0 and _token_key(tokens[start - 1]) in SURNAME_PARTICLES:
        start -= 1
    return tokens[start:]


def _ascii_surname(tokens: list[str]) -> str:
    surname = transliterate_ascii(" ".join(tokens)).strip()
    return re.sub(r"\s+", " ", surname)


def _first_author_surname(author: str) -> tuple[str, bool]:
    text = _strip_trailing_et_al(str(author or ""))
    if not text or is_unknown_author_value(text):
        return UNKNOWN_AUTHOR, False
    if _looks_like_corporate_author(text):
        return UNKNOWN_AUTHOR, False

    first = _first_author_segment(text)
    if not first or is_unknown_author_value(first) or _looks_like_corporate_author(first):
        return UNKNOWN_AUTHOR, False

    if "," in first:
        pieces = [piece.strip() for piece in first.split(",") if piece.strip()]
        if pieces and len(pieces) > 1 and all(_is_suffix_phrase(piece) for piece in pieces[1:]):
            tokens = _surname_tokens_from_space_name(pieces[0])
        else:
            tokens = _strip_suffix_tokens(_name_tokens(pieces[0] if pieces else ""))
    else:
        tokens = _surname_tokens_from_space_name(first)

    surname = _ascii_surname(_strip_suffix_tokens(tokens))
    if slug_word(surname) == UNKNOWN_AUTHOR or not re.search(r"[A-Za-z]", surname):
        return UNKNOWN_AUTHOR, False
    return surname, True


def is_author_key_resolved(author: str) -> bool:
    return _first_author_surname(author)[1]


def first_author_lastname(author: str) -> str:
    return _first_author_surname(author)[0]


def informal_key(metadata: dict[str, Any]) -> str:
    """Generate a citation-key slug from informal metadata.

    Priority: explicit key → title → source_stem → fallback ``note``.
    Keeps ``[a-z0-9_]``, lowercase, collapses underscores, strips edges.
    """

    def _slugify(text: str) -> str:
        if not text:
            return ""
        slug = transliterate_ascii(str(text)).lower()
        slug = re.sub(r"[^a-z0-9_]", "", slug)
        slug = re.sub(r"_+", "_", slug)
        return slug.strip("_")

    for field in ("key", "title", "source_stem"):
        value = metadata.get(field)
        if value is None:
            continue
        result = _slugify(str(value))
        if result:
            return result
    return "note"


def base_key(author: str, year: str) -> str:
    match = re.search(r"\d{4}", str(year))
    year_part = match.group(0) if match else NODATE_YEAR
    return f"{slug_word(first_author_lastname(str(author)))}{year_part}"


def unique_key(category_dir: Path, candidate: str, reserved: set[str] | None = None) -> str:
    reserved = set(reserved or set())

    def available(key: str) -> bool:
        return (
            key not in reserved
            and not (category_dir / f"{key}.md").exists()
            and not (category_dir / f"{key}.bib").exists()
            and not (category_dir / key).exists()
        )

    if available(candidate):
        return candidate
    index = 0
    while True:
        n = index
        chars = []
        while True:
            chars.append(chr(ord("a") + (n % 26)))
            n = n // 26 - 1
            if n < 0:
                break
        suffix = "".join(reversed(chars))
        key = f"{candidate}{suffix}"
        if available(key):
            return key
        index += 1


def is_low_confidence(metadata: dict[str, Any]) -> bool:
    key = base_key(str(metadata.get("author", UNKNOWN_AUTHOR)), str(metadata.get("year", NODATE_YEAR)))
    return (
        str(metadata.get("metadata_confidence") or metadata_confidence(metadata)) == "low"
        or is_unknown_author_value(str(metadata.get("author") or UNKNOWN_AUTHOR))
        or is_nodate_year_value(str(metadata.get("year") or NODATE_YEAR))
        or key.startswith(UNKNOWN_AUTHOR)
    )


def is_remote_or_special_target(target: str) -> bool:
    lowered = target.strip().lower()
    return (
        not lowered
        or lowered.startswith("#")
        or lowered.startswith("/")
        or re.match(r"^[a-z][a-z0-9+.-]*:", lowered) is not None
    )


def is_local_marker_image(target: str) -> bool:
    target = target.strip()
    if is_remote_or_special_target(target):
        return False
    if "/" in target or "\\" in target:
        return False
    suffix = Path(target).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        return False
    return "_page_" in target or "Picture" in target or "Figure" in target


def marker_image_targets(text: str) -> list[str]:
    targets: list[str] = []
    for match in MARKDOWN_IMAGE_RE.finditer(text):
        target = match.group(2)
        if is_local_marker_image(target):
            targets.append(target)
    for match in HTML_SRC_RE.finditer(text):
        target = match.group(2)
        if is_local_marker_image(target):
            targets.append(target)
    return targets


def jsonl_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())


def filed_metadata_paths(library_root: Path) -> list[Path]:
    paths: list[Path] = []
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        paths.extend(sorted(category_dir.glob("*/metadata.json")))
    return paths


def global_filed_keys(library_root: Path) -> set[str]:
    keys: set[str] = set()
    for category in sorted(VALID_CATEGORIES):
        category_dir = library_root / category
        if not category_dir.is_dir():
            continue
        for path in category_dir.iterdir():
            if path.name.startswith("."):
                continue
            if path.is_dir():
                keys.add(path.name)
            elif path.suffix in {".md", ".bib"}:
                keys.add(path.stem)
    return keys


def metadata_sha_set(metadata: dict[str, Any]) -> set[str]:
    sha_values: set[str] = set()
    values = metadata.get("source_sha256")
    if isinstance(values, dict):
        sha_values.update(str(value) for value in values.values() if value)
    if isinstance(values, str) and values:
        sha_values.add(values)
    for row in source_file_rows(metadata.get("source_files")):
        value = row.get("sha256")
        if value:
            sha_values.add(str(value))
    return sha_values


def find_existing_by_sha(library_root: Path, sha_values: set[str]) -> tuple[str, str] | None:
    if not sha_values:
        return None
    from alexandria_key_registry import KeyRegistry

    existing = KeyRegistry.load(library_root).lookup_by_sha(sha_values)
    if existing:
        return existing
    for meta_path in filed_metadata_paths(library_root):
        try:
            metadata = load_json(meta_path)
        except (OSError, ValueError):
            continue
        known_hashes = metadata_sha_set(metadata)
        if sha_values & known_hashes:
            return meta_path.parent.parent.name, meta_path.parent.name
        for source in iter_source_paths(meta_path.parent):
            try:
                if sha256_file(source) in sha_values:
                    return meta_path.parent.parent.name, meta_path.parent.name
            except OSError:
                continue
    return None
