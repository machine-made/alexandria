# Manual Google Scholar BibTeX import fallback

Use this path only when Google Scholar has a better citation than the normal
Crossref/arXiv/ISBN resolvers. This workflow is manual and non-scraping: a person
copies or downloads BibTeX from Scholar, then Alexandria parses that one supplied
entry locally.

## Export one BibTeX entry from Scholar

### Citation UI

1. Open the work in Google Scholar.
2. Click **Cite** (quotation-mark icon) under the result.
3. Click **BibTeX**.
4. Copy the BibTeX entry shown in the browser.

### Scholar library export

1. Open **My library** in Google Scholar.
2. Select the work to export.
3. Choose **Export** / **BibTeX**.
4. Save or copy only the single entry you want to normalize. If the export
   contains multiple entries, split it first; the Alexandria primitive accepts
   exactly one entry to avoid ambiguous merges.

Do not automate or crawl Scholar. This fallback starts from a user-pasted or
user-downloaded `.bib` entry.

## Normalize the entry locally

Paste through stdin:

```bash
printf '%s\n' '@article{smith2020google, title={A Paper}, author={Jane Smith}, year={2020}, doi={10.x/y}}' \
  | pipeline/alexandria_scholar_bib.py
```

Or save the Scholar entry to a file and pass the file path:

```bash
pipeline/alexandria_scholar_bib.py scholar-entry.bib > scholar-entry.metadata.json
```

The command prints Alexandria-shaped metadata JSON. It does not write into the
library, rebuild `alexandria.bib`, edit parsed Markdown, or touch source PDFs.
The downstream merge command is separate work.

## Output and provenance

The normalizer:

- parses the BibTeX entry structurally with stdlib code;
- maps common fields to `title`, `author`, `authors`, `year`, `doi`, `isbn`,
  `arxiv_id`, and a best-effort `category`;
- normalizes the Alexandria citation `key` with `base_key(author, year)`, so the
  key style matches filed works;
- preserves provenance as `scholar_key` (the original BibTeX key) and
  `manual_bib_source` (default: `google-scholar`).

Example output includes:

```json
{
  "key": "smith2020",
  "scholar_key": "smith2020google",
  "manual_bib_source": "google-scholar"
}
```

Corporate, CJK, or otherwise unresolved authors are held rather than guessed:
the original `author` text remains in metadata, but the normalized key becomes an
`unknownYYYY`/`unknownnodate` key and `metadata_confidence` is `low`.

## Parser limits

`pipeline/alexandria_scholar_bib.py` is intentionally focused on one manual
entry. It handles standard braced, quoted, and bare field values, nested braces,
common TeX accents/special characters, and simple `#` concatenation. It does not
expand `@string` macros, `crossref`, or multi-entry exports.
