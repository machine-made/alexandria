#!/usr/bin/env bash
# Drift guard: duplicated identifier surface in pdf_meta vs metadata_common.
# Both modules must behave identically; silent divergence is how 9j1's bug arose.
# pdf_meta imports pypdf — use the project venv (same pattern as test_pdf_meta.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"

[ -x "$PY" ] || { echo "missing .venv python with pypdf: $PY"; exit 1; }

"$PY" - "$ROOT" <<'PY'
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
pipeline = root / "pipeline"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


pm = load("alexandria_pdf_meta", pipeline / "alexandria_pdf_meta.py")
mc = load("alexandria_metadata_common", pipeline / "alexandria_metadata_common.py")

# --- Regex patterns must be identical ---
for name in ("DOI_RE", "ISBN_RE", "ARXIV_RE"):
    p_pat = getattr(pm, name).pattern
    m_pat = getattr(mc, name).pattern
    assert p_pat == m_pat, (
        f"{name}.pattern diverge:\n  pdf_meta={p_pat!r}\n  metadata_common={m_pat!r}"
    )

NORMALIZE_DOI_CASES = [
    ("funder+paren", "10.13039/501100001665)"),
    ("lancet_balanced", "10.1016/S0140-6736(00)02034-9"),
    ("lancet_extra_outer", "10.1016/S0140-6736(00)02034-9)"),
    ("strip_order_paren_period", "10.1234/x.y.)"),
    ("plain", "10.5555/ABC.DEF"),
    ("url_shape_raw", "https://doi.org/10.1234/marker.example"),
    ("trailing_period", "10.5555/abc.def."),
    ("empty", ""),
    ("garbage", "not-a-doi"),
]

EXTRACT_DOI_CASES = [
    ("funder+paren_prose", "funded by the ANR (DOI: 10.13039/501100001665)."),
    ("funder_then_work", "Funding DOI: 10.13039/501100001665 then work DOI: 10.5555/real.work"),
    ("funder_only", "sponsor 10.13039/501100001665 end"),
    ("lancet", "See 10.1016/S0140-6736(00)02034-9."),
    ("plain", "doi: 10.5555/abc.def"),
    ("url_doi", "https://doi.org/10.1234/marker.example"),
    ("strip_order_in_text", "cite (doi: 10.1234/x.y.)."),
    ("empty", ""),
    ("garbage", "no identifiers here"),
]

NORMALIZE_ISBN_CASES = [
    ("isbn13_hyphen", "978-1-4028-9462-6"),
    ("isbn10", "0-306-40615-2"),
    ("empty", ""),
    ("garbage", "ISBN-not-real"),
]

EXTRACT_ISBN_CASES = [
    ("isbn_line", "ISBN 978-1-4028-9462-6"),
    ("empty", ""),
    ("garbage", "no isbn"),
]

EXTRACT_ARXIV_CASES = [
    ("prefix", "arXiv:2401.01234"),
    ("url", "https://arxiv.org/abs/2401.01234"),
    ("empty", ""),
    ("garbage", "no arxiv"),
]


def check_fn(fn_name: str, cases):
    pm_fn = getattr(pm, fn_name)
    mc_fn = getattr(mc, fn_name)
    for label, raw in cases:
        got_pm = pm_fn(raw)
        got_mc = mc_fn(raw)
        assert got_pm == got_mc, (
            f"{fn_name} diverge on {label}: "
            f"pdf_meta={got_pm!r} metadata_common={got_mc!r} input={raw!r}"
        )


check_fn("normalize_doi", NORMALIZE_DOI_CASES)
check_fn("extract_doi", EXTRACT_DOI_CASES)
check_fn("normalize_isbn", NORMALIZE_ISBN_CASES)
check_fn("extract_isbn", EXTRACT_ISBN_CASES)
check_fn("extract_arxiv_id", EXTRACT_ARXIV_CASES)

# Explicit funder-skip: never return 10.13039/ as a work DOI
funder_then_work = "Funding DOI: 10.13039/501100001665 then work DOI: 10.5555/real.work"
assert pm.extract_doi(funder_then_work) == "10.5555/real.work", pm.extract_doi(funder_then_work)
assert mc.extract_doi(funder_then_work) == "10.5555/real.work", mc.extract_doi(funder_then_work)
assert pm.extract_doi("sponsor 10.13039/501100001665 end") == ""
assert mc.extract_doi("sponsor 10.13039/501100001665 end") == ""

# Balance-aware strip
assert pm.normalize_doi("10.1016/S0140-6736(00)02034-9") == "10.1016/s0140-6736(00)02034-9"
assert pm.normalize_doi("10.13039/501100001665)") == "10.13039/501100001665"
assert pm.normalize_doi("10.1234/x.y.)") == "10.1234/x.y"

print("OK identifier parity: pdf_meta ≡ metadata_common on shared surface")
PY
