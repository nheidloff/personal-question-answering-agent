from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import string
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .config import settings
from .ingestion import list_data_files, run_indexing
from .jobs import JobManager
from .model_client import ModelClient
from .models import (
    ChatRequest,
    ChatResponse,
    ChatSource,
    FileMetadataItem,
    IndexJobStatus,
    IndexFilesResponse,
    IndexOverviewResponse,
    RecreateIndexResponse,
    StartIndexResponse,
)
from .opensearch_client import OpenSearchVectorStore

app = FastAPI(title="Personal QA Assistant API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = JobManager()
model = ModelClient(settings)
vector_store = OpenSearchVectorStore(settings)
app.mount("/data", StaticFiles(directory=settings.data_dir), name="data")


@dataclass
class IndexedStateEntry:
    content_sha256: str
    file_modified_utc: datetime | None
    indexed_at_utc: datetime | None


@dataclass
class FailedStateEntry:
    stage: str
    error: str
    last_occurred_utc: datetime | None


def _parse_utc_iso(timestamp_str: str) -> datetime | None:
    normalized = timestamp_str.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _load_index_state_details(
    index_state_file: Path,
) -> tuple[dict[str, IndexedStateEntry], dict[str, FailedStateEntry], datetime | None]:
    if not index_state_file.exists():
        return {}, {}, None

    content = index_state_file.read_text(encoding="utf-8", errors="ignore")
    indexed_entries: dict[str, IndexedStateEntry] = {}
    failed_entries: dict[str, FailedStateEntry] = {}
    synchronized_at: datetime | None = None
    section: str | None = None

    for line in content.splitlines():
        row = line.strip()
        row_lower = row.lower()
        if row_lower.startswith("last synchronized (utc):"):
            _, _, value = row.partition(":")
            parsed = _parse_utc_iso(value)
            if parsed is not None:
                synchronized_at = parsed
            continue

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

        if section == "indexed":
            if len(columns) != 4:
                continue
            path, content_sha256, modified_at_str, indexed_at_str = columns
            if not path or not content_sha256:
                continue
            if len(content_sha256) != 64 or any(char not in string.hexdigits for char in content_sha256):
                continue
            indexed_entries[path] = IndexedStateEntry(
                content_sha256=content_sha256,
                file_modified_utc=_parse_utc_iso(modified_at_str),
                indexed_at_utc=_parse_utc_iso(indexed_at_str),
            )
            continue

        if section == "failed":
            if len(columns) not in {3, 4}:
                continue
            path = columns[0]
            if not path or path == "_None_":
                continue
            stage = columns[1]
            error = columns[2]
            last_occurred = _parse_utc_iso(columns[3]) if len(columns) == 4 else None
            failed_entries[path] = FailedStateEntry(
                stage=stage,
                error=error,
                last_occurred_utc=last_occurred,
            )

    return indexed_entries, failed_entries, synchronized_at


def _load_index_state_snapshot(index_state_file: Path) -> tuple[set[str], int, datetime | None]:
    indexed_entries, failed_entries, synchronized_at = _load_index_state_details(index_state_file)
    return set(indexed_entries.keys()), len(failed_entries), synchronized_at


def _run_index_job(job_id: str) -> None:
    def on_progress(progress: float, message: str) -> None:
        jobs.update(job_id, status="running", progress=min(progress, 100), message=message)

    jobs.update(job_id, status="running", progress=1, message="Starting indexing")
    try:
        result = run_indexing(settings, model, vector_store, on_progress)
        jobs.update(job_id, status="completed", progress=100, message="Indexing completed", result=result)
    except Exception as exc:  # noqa: BLE001
        jobs.update(job_id, status="failed", message="Indexing failed", error=str(exc))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/index/start", response_model=StartIndexResponse)
def start_index(background_tasks: BackgroundTasks) -> StartIndexResponse:
    job = jobs.create()
    background_tasks.add_task(_run_index_job, job.job_id)
    return StartIndexResponse(job_id=job.job_id)


@app.get("/api/index/status/{job_id}", response_model=IndexJobStatus)
def get_index_status(job_id: str) -> IndexJobStatus:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/index/overview", response_model=IndexOverviewResponse)
def get_index_overview() -> IndexOverviewResponse:
    data_dir = Path(settings.data_dir)
    index_state_file = Path(settings.index_state_file)
    indexed_paths, failed_files, last_synchronized = _load_index_state_snapshot(index_state_file)

    data_files_total = 0
    if data_dir.exists() and data_dir.is_dir():
        data_files_total = len(list_data_files(data_dir))

    opensearch_status: str = "ok"
    opensearch_error: str | None = None
    indexed_paths_in_opensearch: set[str] | None = None
    opensearch_chunks_count: int | None = None
    try:
        indexed_paths_in_opensearch = vector_store.get_indexed_paths()
        opensearch_chunks_count = vector_store.count_documents()
    except Exception as exc:  # noqa: BLE001
        opensearch_status = "unavailable"
        opensearch_error = str(exc)

    if indexed_paths_in_opensearch is None:
        indexed_files = len(indexed_paths)
        indexed_files_opensearch_count = None
    else:
        indexed_files = len(indexed_paths & indexed_paths_in_opensearch)
        indexed_files_opensearch_count = len(indexed_paths_in_opensearch)

    unindexed_data_files = max(data_files_total - indexed_files, 0)

    return IndexOverviewResponse(
        indexed_files=indexed_files,
        failed_files=failed_files,
        last_synchronized_utc=last_synchronized,
        data_files_total=data_files_total,
        unindexed_data_files=unindexed_data_files,
        indexed_files_state_count=len(indexed_paths),
        indexed_files_opensearch_count=indexed_files_opensearch_count,
        opensearch_chunks_count=opensearch_chunks_count,
        opensearch_status=opensearch_status,
        opensearch_error=opensearch_error,
        data_dir=str(data_dir.absolute()),
    )


@app.get("/api/index/files", response_model=IndexFilesResponse)
def get_index_files() -> IndexFilesResponse:
    data_dir = Path(settings.data_dir)
    index_state_file = Path(settings.index_state_file)
    indexed_entries, failed_entries, synchronized_at = _load_index_state_details(index_state_file)

    data_paths: list[str] = []
    if data_dir.exists() and data_dir.is_dir():
        data_paths = [str(path.relative_to(data_dir.parent)) for path in list_data_files(data_dir)]

    files: list[FileMetadataItem] = []
    for path in sorted(data_paths, key=str.casefold):
        indexed = indexed_entries.get(path)
        failed = failed_entries.get(path)

        if failed is not None:
            status = "failed"
        elif indexed is not None:
            status = "indexed"
        else:
            status = "unindexed"

        files.append(
            FileMetadataItem(
                path=path,
                status=status,
                content_sha256=indexed.content_sha256 if indexed else None,
                file_modified_utc=indexed.file_modified_utc if indexed else None,
                indexed_at_utc=indexed.indexed_at_utc if indexed else None,
                stage=failed.stage if failed else None,
                error=failed.error if failed else None,
                last_occurred_utc=failed.last_occurred_utc if failed else None,
            )
        )

    return IndexFilesResponse(
        last_synchronized_utc=synchronized_at,
        files=files,
    )


@app.post("/api/index/recreate", response_model=RecreateIndexResponse)
def recreate_index() -> RecreateIndexResponse:
    index_state_file = Path(settings.index_state_file)
    index_state_template_file = Path(settings.index_state_template_file)

    if not index_state_template_file.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Template file not found: {index_state_template_file}",
        )

    try:
        if index_state_file.exists():
            index_state_file.unlink()

        index_state_file.parent.mkdir(parents=True, exist_ok=True)
        index_state_file.write_text(index_state_template_file.read_text(encoding="utf-8"), encoding="utf-8")

        vector_dimension = vector_store.get_index_vector_dimension()
        if vector_dimension is None:
            probe_vectors = model.embed_texts(["index reset probe"])
            if not probe_vectors or not probe_vectors[0]:
                raise RuntimeError("Embedding model returned no vector for index re-creation")
            vector_dimension = len(probe_vectors[0])

        documents_removed = vector_store.remove_all_documents()
        index_deleted = vector_store.delete_index()
        vector_store.ensure_index(vector_dimension, recreate=False)

        return RecreateIndexResponse(
            status="completed",
            message="Index and index-files.md were re-created successfully",
            index_state_file=str(index_state_file),
            index_state_template_file=str(index_state_template_file),
            opensearch_index=vector_store.index_name,
            documents_removed=documents_removed,
            index_deleted=index_deleted,
            index_created=vector_store.index_exists(),
            vector_dimension=vector_dimension,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to re-create index: {exc}") from exc


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")

    query_vectors = model.embed_texts([question])
    if not query_vectors:
        raise HTTPException(status_code=500, detail="Embedding model returned no vector")

    try:
        hits = vector_store.search(query_vectors[0], settings.retrieval_top_k)
    except Exception:  # noqa: BLE001
        return ChatResponse(
            answer="I could not query indexed data. Please run indexing first.",
            sources=[],
        )
    context_blocks: list[str] = []
    sources: list[ChatSource] = []

    for hit in hits:
        source = hit.get("_source", {})
        text = source.get("text", "")
        path = source.get("path", "")
        score = float(hit.get("_score", 0.0))
        if text:
            context_blocks.append(f"Source: {path}\n{text}")
            sources.append(
                ChatSource(
                    path=path,
                    score=score,
                    snippet=text[:240],
                )
            )

    if not context_blocks:
        return ChatResponse(
            answer="I could not find relevant indexed context. Please run indexing first or add more documents.",
            sources=[],
        )

    system_prompt = (
        "You are a personal knowledge assistant. Answer using only the provided context. "
        "If the answer is not in the context, say you don't know. "
        "Cite sources by filename when possible."
    )

    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Context:\n{'\n\n'.join(context_blocks)}\n\n"
        "Provide a concise answer."
    )

    answer = model.chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
    )

    return ChatResponse(answer=answer, sources=sources)
