from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from pathlib import Path
from typing import Callable
import string
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    WordFormatOption,
    PowerpointFormatOption,
    ImageFormatOption,
    HTMLFormatOption,
)
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from .config import Settings
from .model_client import ModelClient
from .opensearch_client import OpenSearchVectorStore

from typing import Any, Callable

ProgressCallback = Callable[[float, str, dict[str, Any] | None], None]

IGNORED_FILENAMES = {".DS_Store"}

@dataclass
class ChunkRecord:
    id: str
    path: str
    filename: str
    chunk_id: str
    text: str


@dataclass
class FileFingerprint:
    path: str
    file_path: Path
    content_sha256: str
    modified_at: datetime


@dataclass
class IndexedFileState:
    path: str
    content_sha256: str
    modified_at: datetime
    indexed_at: datetime


@dataclass
class FailedFileState:
    path: str
    stage: str
    error: str
    last_occurred_at: datetime


@lru_cache(maxsize=1)
def _get_document_converter() -> DocumentConverter:
    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.IMAGE,
            InputFormat.DOCX,
            InputFormat.HTML,
            InputFormat.PPTX,
            InputFormat.ASCIIDOC,
            InputFormat.CSV,
            InputFormat.MD,
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=StandardPdfPipeline, backend=PyPdfiumDocumentBackend
            ),
            InputFormat.DOCX: WordFormatOption(
                pipeline_cls=SimplePipeline
            ),
            InputFormat.IMAGE: ImageFormatOption(
                pipeline_options=PdfPipelineOptions(
                    do_ocr=True,
                    ocr_options=EasyOcrOptions(use_gpu=False),
                )
            ),
        },
    )


def _read_with_docling(file_path: Path, settings: Settings | None = None) -> str:
    conversion = _get_document_converter().convert(file_path)
    markdown_content = conversion.document.export_to_markdown()

    if settings:
        try:
            # We want to save representations in data-text/markdown and data-text/doctags
            data_text_dir = Path(settings.data_text_dir)
            
            # Use relative path from data_dir to preserve structure
            rel_path = file_path.relative_to(settings.data_dir)
            
            markdown_dir = data_text_dir / "markdown"
            doctags_dir = data_text_dir / "doctags"
            
            markdown_file = (markdown_dir / rel_path).with_suffix(".md")
            doctags_file = (doctags_dir / rel_path).with_suffix(".doctags")
            
            markdown_file.parent.mkdir(parents=True, exist_ok=True)
            doctags_file.parent.mkdir(parents=True, exist_ok=True)
            
            with markdown_file.open("w", encoding="utf-8") as fp:
                fp.write(markdown_content)
            
            with doctags_file.open("w", encoding="utf-8") as fp:
                fp.write(conversion.document.export_to_doctags())
        except Exception:
            # If saving fails, we still want the extraction to count
            pass

    return markdown_content


SUPPORTED_DOCLING_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".html", ".htm", ".asciidoc", ".adoc",
    ".csv", ".xlsx", ".xls", ".xml", ".xhtml", ".latex", ".tex",
    ".png", ".jpg", ".jpeg", ".tiff", ".bmp"
}


def extract_text_from_file(file_path: Path) -> str:
    suffix = file_path.suffix.lower()

    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8")

    if suffix in SUPPORTED_DOCLING_EXTENSIONS or suffix == ".md":
        try:
            return _read_with_docling(file_path, settings=Settings())
        except Exception:
            # Fallback for markdown only
            if suffix == ".md":
                return file_path.read_text(encoding="utf-8")
            raise

    # Attempt any other file type as UTF-8 text.
    return file_path.read_text(encoding="utf-8")


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(normalized)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break

        start = max(end - overlap, 0)

    return chunks


def list_data_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in data_dir.rglob("*"):
        if path.is_file() and path.name not in IGNORED_FILENAMES:
            files.append(path)
    return sorted(files)


def _to_utc_iso(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_iso(timestamp_str: str) -> datetime:
    normalized = timestamp_str.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _relative_file_path(file_path: Path, data_dir: Path) -> str:
    return str(file_path.relative_to(data_dir.parent))


def _is_markdown_separator(columns: list[str]) -> bool:
    return bool(columns) and all(column and set(column) <= {"-", ":"} for column in columns)


def _split_markdown_row(row: str) -> list[str]:
    if not row.startswith("|"):
        return []

    columns: list[str] = []
    current: list[str] = []
    idx = 1
    while idx < len(row):
        char = row[idx]
        if char == "|" and (idx == 0 or row[idx - 1] != "\\"):
            columns.append("".join(current).strip())
            current = []
            idx += 1
            continue

        if char == "\\" and idx + 1 < len(row) and row[idx + 1] == "|":
            current.append("|")
            idx += 2
            continue

        current.append(char)
        idx += 1

    if current:
        columns.append("".join(current).strip())
    return columns


def _load_index_state(index_state_file: Path) -> tuple[dict[str, IndexedFileState], set[str]]:
    if not index_state_file.exists():
        return {}, set()

    content = index_state_file.read_text(encoding="utf-8", errors="ignore")
    state: dict[str, IndexedFileState] = {}
    indexed_paths: set[str] = set()
    section: str | None = None

    for line in content.splitlines():
        row = line.strip()
        row_lower = row.lower()
        if row_lower.startswith("## successfully indexed files"):
            section = "indexed"
            continue

        if row_lower.startswith("## failed files"):
            section = "failed"
            continue

        if not row.startswith("|"):
            continue

        columns = _split_markdown_row(row)
        if not columns or columns[0].lower() == "path" or _is_markdown_separator(columns):
            continue

        path = columns[0]
        if not path or path == "_None_":
            continue

        indexed_paths.add(path)

        if section != "indexed":
            continue

        if len(columns) != 4:
            continue

        _, content_sha256, modified_at_str, indexed_at_str = columns
        if len(content_sha256) != 64 or any(char not in string.hexdigits for char in content_sha256):
            continue

        try:
            state[path] = IndexedFileState(
                path=path,
                content_sha256=content_sha256,
                modified_at=_parse_utc_iso(modified_at_str),
                indexed_at=_parse_utc_iso(indexed_at_str),
            )
        except ValueError:
            continue

    return state, indexed_paths


def _write_index_state(
    index_state_file: Path,
    entries: list[IndexedFileState],
    failed_entries: list[FailedFileState],
    synchronized_at: datetime,
) -> None:
    lines = [
        f"Last synchronized (UTC): {_to_utc_iso(synchronized_at)}",
        "",
        "# Indexed Files State",
        "",
        "## Successfully Indexed Files",
        "",
        "| Path | Content SHA256 | File Modified (UTC) | Indexed At (UTC) |",
        "| --- | --- | --- | --- |",
    ]

    for entry in sorted(entries, key=lambda item: item.path):
        safe_path = entry.path.replace("|", "\\|")
        lines.append(
            f"| {safe_path} | {entry.content_sha256} | {_to_utc_iso(entry.modified_at)} | {_to_utc_iso(entry.indexed_at)} |"
        )

    lines.extend(
        [
            "",
            "## Failed Files",
            "",
            "| Path | Stage | Error | Last Occurred (UTC) |",
            "| --- | --- | --- | --- |",
        ]
    )
    if failed_entries:
        for entry in sorted(failed_entries, key=lambda item: item.path):
            safe_path = entry.path.replace("|", "\\|")
            safe_stage = entry.stage.replace("|", "\\|")
            safe_error = " ".join(entry.error.splitlines()).replace("|", "\\|")
            lines.append(
                f"| {safe_path} | {safe_stage} | {safe_error} | {_to_utc_iso(entry.last_occurred_at)} |"
            )
    else:
        lines.append("| _None_ | - | - | - |")

    index_state_file.parent.mkdir(parents=True, exist_ok=True)
    index_state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_file_fingerprints(
    data_dir: Path, on_progress: ProgressCallback
) -> tuple[list[FileFingerprint], set[str], list[FailedFileState]]:
    files = list_data_files(data_dir)
    if not files:
        return [], set(), []

    fingerprints: list[FileFingerprint] = []
    discovered_paths: set[str] = set()
    failed: list[FailedFileState] = []
    total = len(files)

    for idx, file_path in enumerate(files, start=1):
        on_progress(2 + (idx / max(total, 1)) * 18, f"Scanning {file_path.name}", None)
        rel_path = _relative_file_path(file_path, data_dir)
        discovered_paths.add(rel_path)
        try:
            stat = file_path.stat()
            fingerprints.append(
                FileFingerprint(
                    path=rel_path,
                    file_path=file_path,
                    content_sha256=_hash_file(file_path),
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            on_progress(2 + (idx / max(total, 1)) * 18, f"Skipping {file_path.name}: {exc}", None)
            failed.append(
                FailedFileState(
                    path=rel_path,
                    stage="scan",
                    error=str(exc),
                    last_occurred_at=datetime.now(timezone.utc),
                )
            )

    return fingerprints, discovered_paths, failed


def build_chunks_for_files(
    files: list[FileFingerprint],
    settings: Settings,
    on_progress: ProgressCallback,
    progress_start: float,
    progress_span: float,
) -> tuple[list[ChunkRecord], set[str], list[FailedFileState]]:
    if not files:
        return [], set(), []

    records: list[ChunkRecord] = []
    successful_paths: set[str] = set()
    failed: list[FailedFileState] = []
    total = len(files)
    for idx, file in enumerate(files, start=1):
        progress = progress_start + (idx / max(total, 1)) * progress_span
        on_progress(progress, f"Extracting {file.file_path.name}", None)
        try:
            text = extract_text_from_file(file.file_path)
            successful_paths.add(file.path)
            on_progress(progress, f"Extracted {file.file_path.name}", {"indexed_paths": sorted(list(successful_paths))})
        except Exception as exc:  # noqa: BLE001
            on_progress(progress, f"Skipping {file.file_path.name}: {exc}", None)
            failed.append(
                FailedFileState(
                    path=file.path,
                    stage="extract",
                    error=str(exc),
                    last_occurred_at=datetime.now(timezone.utc),
                )
            )
            continue

        chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap)

        for chunk_idx, chunk in enumerate(chunks, start=1):
            records.append(
                ChunkRecord(
                    id=f"{file.path}:{chunk_idx}",
                    path=file.path,
                    filename=file.file_path.name,
                    chunk_id=str(chunk_idx),
                    text=chunk,
                )
            )

    return records, successful_paths, failed


def _build_docs(chunks: list[ChunkRecord], vectors: list[list[float]]) -> list[dict[str, object]]:
    if len(chunks) != len(vectors):
        raise RuntimeError("Embedding model returned a mismatched number of vectors")

    docs: list[dict[str, object]] = []
    for chunk, embedding in zip(chunks, vectors):
        docs.append(
            {
                "id": chunk.id,
                "path": chunk.path,
                "filename": chunk.filename,
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "embedding": embedding,
            }
        )
    return docs


def _index_chunks(
    chunks: list[ChunkRecord],
    settings: Settings,
    model_client: ModelClient,
    store: OpenSearchVectorStore,
    on_progress: ProgressCallback,
) -> int:
    if not chunks:
        return 0

    total = len(chunks)
    first_batch_size = min(settings.embedding_batch_size, total)
    first_batch = chunks[:first_batch_size]
    first_vectors = model_client.embed_texts([chunk.text for chunk in first_batch])
    if not first_vectors:
        raise RuntimeError("Embedding model returned no vectors")

    vector_dim = len(first_vectors[0])
    store.ensure_index(vector_dim, recreate=False)

    first_docs = _build_docs(first_batch, first_vectors)
    if first_docs:
        store.bulk_index(first_docs)

    indexed_count = len(first_docs)
    successfully_indexed_paths = {doc["path"] for doc in first_docs}
    on_progress(
        50 + (indexed_count / max(total, 1)) * 50,
        f"Indexed {indexed_count}/{total} chunks",
        {"indexed_paths": sorted(list(successfully_indexed_paths))}
    )

    for start in range(first_batch_size, total, settings.embedding_batch_size):
        end = min(start + settings.embedding_batch_size, total)
        batch = chunks[start:end]
        vectors = model_client.embed_texts([chunk.text for chunk in batch])
        docs = _build_docs(batch, vectors)
        if docs:
            store.bulk_index(docs)
            indexed_count += len(docs)
            for doc in docs:
                successfully_indexed_paths.add(doc["path"])
        percent = 50 + (indexed_count / max(total, 1)) * 50
        on_progress(
            percent,
            f"Indexed {indexed_count}/{total} chunks",
            {"indexed_paths": sorted(list(successfully_indexed_paths))}
        )

    return indexed_count


def run_indexing(
    settings: Settings,
    model_client: ModelClient,
    store: OpenSearchVectorStore,
    on_progress: ProgressCallback,
) -> dict[str, int]:
    data_dir = Path(settings.data_dir)
    if not data_dir.exists() or not data_dir.is_dir():
        raise RuntimeError(f"Data directory does not exist: {data_dir}")

    index_state_file = Path(settings.index_state_file)
    previous_state, previous_indexed_paths = _load_index_state(index_state_file)

    on_progress(2, "Scanning files", None)
    fingerprints, discovered_paths, failed_entries = build_file_fingerprints(data_dir, on_progress)
    current_paths = set(discovered_paths)
    previous_paths = set(previous_indexed_paths)

    deleted_paths = sorted(previous_paths - current_paths)
    files_to_index: list[FileFingerprint] = []
    unchanged_paths: list[str] = []

    for file in fingerprints:
        existing = previous_state.get(file.path)
        if existing is None or existing.content_sha256 != file.content_sha256:
            files_to_index.append(file)
        else:
            unchanged_paths.append(file.path)

    if not files_to_index and not deleted_paths:
        synchronized_at = datetime.now(timezone.utc)
        _write_index_state(index_state_file, list(previous_state.values()), failed_entries, synchronized_at)
        on_progress(100, "No new or changed files to index", None)
        return {
            "files_processed": len(discovered_paths),
            "files_indexed": 0,
            "files_unchanged": len(unchanged_paths),
            "files_deleted": 0,
            "files_failed": len(failed_entries),
            "chunks_indexed": 0,
        }

    if deleted_paths:
        on_progress(25, f"Removing {len(deleted_paths)} deleted file(s) from index", None)
        store.delete_by_paths(deleted_paths)
        
        # Also delete corresponding text files
        data_text_dir = Path(settings.data_text_dir)
        data_dir_name = Path(settings.data_dir).name
        for path_str in deleted_paths:
            path = Path(path_str)
            if path.parts[0] == data_dir_name:
                # Get the relative path inside data/
                rel_path = Path(*path.parts[1:])
                
                markdown_file = (data_text_dir / "markdown" / rel_path).with_suffix(".md")
                doctags_file = (data_text_dir / "doctags" / rel_path).with_suffix(".doctags")
                
                try:
                    if markdown_file.exists():
                        markdown_file.unlink()
                    if doctags_file.exists():
                        doctags_file.unlink()
                except Exception:
                    pass

    chunks, successful_paths, extraction_failures = build_chunks_for_files(
        files_to_index,
        settings,
        on_progress,
        progress_start=30,
        progress_span=20,
    )
    failed_entries.extend(extraction_failures)
    failed_paths = sorted({entry.path for entry in failed_entries})

    reindex_paths = sorted(successful_paths)
    if reindex_paths:
        on_progress(52, f"Removing outdated chunks for {len(reindex_paths)} updated file(s)", None)
        store.delete_by_paths(reindex_paths)

    indexed_count = 0
    if chunks:
        on_progress(55, "Generating embeddings and indexing", None)
        indexed_count = _index_chunks(chunks, settings, model_client, store, on_progress)
    else:
        on_progress(90, "No new chunk content to index", None)

    synchronized_at = datetime.now(timezone.utc)
    next_state: dict[str, IndexedFileState] = {}

    fingerprint_by_path = {file.path: file for file in fingerprints}
    for path in sorted(discovered_paths):
        file = fingerprint_by_path.get(path)
        if file is None:
            previous = previous_state.get(path)
            if previous is not None:
                next_state[path] = previous
            continue

        if file.path in successful_paths:
            next_state[file.path] = IndexedFileState(
                path=file.path,
                content_sha256=file.content_sha256,
                modified_at=file.modified_at,
                indexed_at=synchronized_at,
            )
            continue

        previous = previous_state.get(file.path)
        if previous is not None:
            # Keep previous successful index state if this run skipped or failed this file.
            next_state[file.path] = previous

    _write_index_state(index_state_file, list(next_state.values()), failed_entries, synchronized_at)
    on_progress(100, f"Completed indexing {indexed_count} chunks", {"indexed_paths": sorted(list(successful_paths))})

    return {
        "files_processed": len(discovered_paths),
        "files_indexed": len(successful_paths),
        "files_unchanged": len(unchanged_paths),
        "files_deleted": len(deleted_paths),
        "files_failed": len(failed_paths),
        "chunks_indexed": indexed_count,
    }
