from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field

JobStatusLiteral = Literal["queued", "running", "completed", "failed"]
FileMetadataStatusLiteral = Literal["indexed", "failed", "unindexed"]

class StartIndexResponse(BaseModel):
    job_id: str

class IndexJobStatus(BaseModel):
    job_id: str
    status: JobStatusLiteral
    progress: float = 0.0
    message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    result: dict[str, Any] | None = None
    error: str | None = None

class ChatRequest(BaseModel):
    question: str

class ChatSource(BaseModel):
    path: str
    score: float
    snippet: str

class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]

class RecreateIndexResponse(BaseModel):
    status: JobStatusLiteral
    message: str
    index_state_file: str
    index_state_template_file: str
    opensearch_index: str
    documents_removed: int
    index_deleted: bool
    index_created: bool
    vector_dimension: int

class IndexOverviewResponse(BaseModel):
    indexed_files: int
    failed_files: int
    last_synchronized_utc: datetime | None = None
    data_files_total: int
    unindexed_data_files: int
    indexed_files_state_count: int
    indexed_files_opensearch_count: int | None = None
    opensearch_chunks_count: int | None = None
    opensearch_status: Literal["ok", "unavailable"] = "ok"
    opensearch_error: str | None = None

class FileMetadataItem(BaseModel):
    path: str
    status: FileMetadataStatusLiteral
    content_sha256: str | None = None
    file_modified_utc: datetime | None = None
    indexed_at_utc: datetime | None = None
    stage: str | None = None
    error: str | None = None
    last_occurred_utc: datetime | None = None

class IndexFilesResponse(BaseModel):
    last_synchronized_utc: datetime | None = None
    files: list[FileMetadataItem]