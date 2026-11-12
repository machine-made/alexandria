#!/usr/bin/env python3
"""Compile Alexandria BibLaTeX output with biber as a structural smoke test."""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from alexandria_bibtex import rebuild_root_bibliography

JOB_NAME = "alexandria_latex_smoke"
BIB_NAME = f"{JOB_NAME}.bib"
TEX_NAME = f"{JOB_NAME}.tex"
PDF_NAME = f"{JOB_NAME}.pdf"
COMMAND_TIMEOUT_SECONDS = 60
OUTPUT_TAIL_CHARS = 5000


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def display_command(self) -> str:
        return " ".join(self.command)


@dataclass(frozen=True)
class Diagnostic:
    kind: str
    message: str
    entry: str | None = None

    def format(self) -> str:
        if self.entry:
            return f"{self.kind}: entry {self.entry}: {self.message}"
        return f"{self.kind}: {self.message}"


@dataclass(frozen=True)
class BibEntrySpan:
    key: str
    start_line: int
    end_line: int


ENTRY_START_RE = re.compile(r"^\s*@\s*[A-Za-z]+\s*\{\s*([^,\s]+)")
BIBER_LEVEL_RE = re.compile(r"\b(WARN|ERROR)\s+-\s+(.*)")
BIBER_LINE_RE = re.compile(r"\bline\s+(\d+)\b", re.IGNORECASE)
LOG_UNDEFINED_CITATION_RE = re.compile(r"Citation [`']([^`']+)' undefined")
BBL_ENTRY_RE = re.compile(r"\\entry\{")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--library", type=Path, help="library root; rebuild and use <root>/alexandria.bib")
    inputs.add_argument("--bib", type=Path, help="BibTeX/BibLaTeX file to compile directly")
    parser.add_argument("--engine", default="pdflatex", help="LaTeX engine to run (default: pdflatex)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary build directory for debugging")
    return parser.parse_args()


def resolve_bib(args: argparse.Namespace) -> Path:
    if args.library:
        library_root = args.library.resolve()
        if not library_root.is_dir():
            raise ValueError(f"library root does not exist or is not a directory: {library_root}")
        return rebuild_root_bibliography(library_root).resolve()
    bib = args.bib.resolve()
    if not bib.is_file():
        raise ValueError(f"BibTeX file does not exist: {bib}")
    return bib


def missing_toolchain(engine: str) -> list[str]:
    missing: list[str] = []
    if shutil.which(engine) is None:
        missing.append(engine)
    if shutil.which("biber") is None:
        missing.append("biber")
    return missing


def build_tex(bib_filename: str) -> str:
    return "\n".join(
        [
            r"\documentclass{article}",
            r"\usepackage[backend=biber]{biblatex}",
            rf"\addbibresource{{{bib_filename}}}",
            r"\begin{document}",
            r"\nocite{*}",
            r"\printbibliography",
            r"\end{document}",
            "",
        ]
    )


def create_build_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix=".alexandria-latex-smoke-", dir=Path.cwd())).resolve()


def run_command(command: list[str], build_dir: Path) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=build_dir,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            tuple(command),
            124,
            exc.stdout or "",
            (exc.stderr or "") + f"\nTimed out after {COMMAND_TIMEOUT_SECONDS} seconds.",
            timed_out=True,
        )
    return CommandResult(tuple(command), completed.returncode, completed.stdout, completed.stderr)


def run_latexmk(build_dir: Path) -> list[CommandResult]:
    return [
        run_command(
            [
                "latexmk",
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                TEX_NAME,
            ],
            build_dir,
        )
    ]


def run_manual(engine: str, build_dir: Path) -> list[CommandResult]:
    commands = [
        [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", TEX_NAME],
        ["biber", JOB_NAME],
        [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", TEX_NAME],
        [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", TEX_NAME],
    ]
    results: list[CommandResult] = []
    for command in commands:
        result = run_command(command, build_dir)
        results.append(result)
        if result.returncode != 0:
            break
    return results


def run_compile(engine: str, build_dir: Path) -> list[CommandResult]:
    if engine == "pdflatex" and shutil.which("latexmk") is not None:
        return run_latexmk(build_dir)
    return run_manual(engine, build_dir)


def bib_entry_spans(bib_path: Path) -> list[BibEntrySpan]:
    lines = bib_path.read_text(encoding="utf-8", errors="replace").splitlines()
    starts: list[tuple[str, int]] = []
    for line_number, line in enumerate(lines, start=1):
        match = ENTRY_START_RE.match(line)
        if match:
            starts.append((match.group(1).strip(), line_number))
    spans: list[BibEntrySpan] = []
    for idx, (key, start_line) in enumerate(starts):
        end_line = starts[idx + 1][1] - 1 if idx + 1 < len(starts) else len(lines)
        spans.append(BibEntrySpan(key, start_line, end_line))
    return spans


def entry_for_line(spans: list[BibEntrySpan], line_number: int | None) -> str | None:
    if line_number is None:
        return None
    for span in spans:
        if span.start_line <= line_number <= span.end_line:
            return span.key
    return None


def line_number_from_biber(message: str) -> int | None:
    match = BIBER_LINE_RE.search(message)
    if not match:
        return None
    return int(match.group(1))


def key_from_message(message: str) -> str | None:
    patterns = (
        r"database entry for ['\"]([^'\"]+)['\"]",
        r"entry ['\"]([^'\"]+)['\"]",
        r"key ['\"]([^'\"]+)['\"]",
        r"citekey ['\"]([^'\"]+)['\"]",
        r"citation ['\"]([^'\"]+)['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def diagnostics_from_blg(build_dir: Path, spans: list[BibEntrySpan]) -> list[Diagnostic]:
    blg_path = build_dir / f"{JOB_NAME}.blg"
    if not blg_path.is_file():
        return []
    diagnostics: list[Diagnostic] = []
    for line in blg_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = BIBER_LEVEL_RE.search(line)
        if not match:
            continue
        level, message = match.groups()
        entry = key_from_message(message) or entry_for_line(spans, line_number_from_biber(message))
        diagnostics.append(Diagnostic(f"biber {level}", message.strip(), entry))
    return diagnostics


def diagnostics_from_log(build_dir: Path) -> list[Diagnostic]:
    log_path = build_dir / f"{JOB_NAME}.log"
    if not log_path.is_file():
        return []
    diagnostics: list[Diagnostic] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        undefined = LOG_UNDEFINED_CITATION_RE.search(line)
        if undefined:
            key = undefined.group(1)
            diagnostics.append(Diagnostic("undefined citation", line.strip(), key))
        if "Empty bibliography" in line:
            diagnostics.append(Diagnostic("empty bibliography", line.strip()))
    return diagnostics


def diagnostics_from_bibliography(build_dir: Path, spans: list[BibEntrySpan]) -> list[Diagnostic]:
    bbl_path = build_dir / f"{JOB_NAME}.bbl"
    if not spans:
        return [Diagnostic("empty bibliography", "no BibTeX entries found in input bibliography")]
    if bbl_path.is_file():
        bbl = bbl_path.read_text(encoding="utf-8", errors="replace")
        if not BBL_ENTRY_RE.search(bbl):
            return [Diagnostic("empty bibliography", "biber produced no bibliography entries")]
    return []


def collect_diagnostics(build_dir: Path, bib_path: Path) -> list[Diagnostic]:
    spans = bib_entry_spans(bib_path)
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(diagnostics_from_blg(build_dir, spans))
    diagnostics.extend(diagnostics_from_log(build_dir))
    diagnostics.extend(diagnostics_from_bibliography(build_dir, spans))
    return diagnostics


def output_tail(text: str) -> str:
    if len(text) <= OUTPUT_TAIL_CHARS:
        return text
    return "...<truncated>...\n" + text[-OUTPUT_TAIL_CHARS:]


def print_failure(bib_path: Path, build_dir: Path, diagnostics: list[Diagnostic], results: list[CommandResult]) -> None:
    print(f"latex smoke failed for: {bib_path}")
    print(f"build-dir: {build_dir}")
    if diagnostics:
        print("diagnostics:")
        for diagnostic in diagnostics:
            print(f"- {diagnostic.format()}")
    failing = [result for result in results if result.returncode != 0 or result.timed_out]
    if failing:
        print("failed commands:")
        for result in failing:
            print(f"$ {result.display_command}")
            print(f"exit: {result.returncode}")
            stdout = output_tail(result.stdout).strip()
            stderr = output_tail(result.stderr).strip()
            if stdout:
                print("stdout:")
                print(stdout)
            if stderr:
                print("stderr:")
                print(stderr)
    elif not diagnostics:
        print("diagnostics: no parser diagnostic was found, but the expected PDF was not produced")


def compile_smoke(bib_path: Path, engine: str, keep: bool) -> int:
    missing = missing_toolchain(engine)
    if missing:
        print(f"skipped: no LaTeX toolchain (missing: {', '.join(missing)})")
        return 0

    build_dir = create_build_dir()
    try:
        build_bib = build_dir / BIB_NAME
        shutil.copy2(bib_path, build_bib)
        (build_dir / TEX_NAME).write_text(build_tex(BIB_NAME), encoding="utf-8")
        results = run_compile(engine, build_dir)
        diagnostics = collect_diagnostics(build_dir, build_bib)
        pdf_path = build_dir / PDF_NAME
        failed_command = any(result.returncode != 0 for result in results)
        if failed_command or diagnostics or not pdf_path.is_file() or pdf_path.stat().st_size == 0:
            print_failure(bib_path, build_dir, diagnostics, results)
            return 1
        print(f"latex smoke succeeded: {len(bib_entry_spans(build_bib))} entries resolved from {bib_path}")
        print(f"build-dir: {build_dir}")
        print(f"pdf: {pdf_path}")
        return 0
    finally:
        if not keep:
            shutil.rmtree(build_dir, ignore_errors=True)


def main() -> int:
    args = parse_args()
    try:
        bib_path = resolve_bib(args)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    return compile_smoke(bib_path, args.engine, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
