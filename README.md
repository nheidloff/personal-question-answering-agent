# Personal Question Answering Agent

Web application for asking questions over personal files with:
- Frontend: React
- Backend: Python (FastAPI)
- Chat model: `gpt-oss:20b` via (`/v1/chat/completions`)
- Embedding model: `nomic-embed-text-v1.5` via (`/v1/embeddings`)
- Vector database: OpenSearch (k-NN vector index)

All file types in `data/` (including sub-directories) are scanned and attempted.
Known extractors are used for `.txt`, `.md`, `.pdf`, `.jpeg`, `.jpg`, `.png`; all other files are attempted as UTF-8 text.
Files that cannot be read or extracted are skipped and recorded as failed.

## Architecture

1. User clicks **Start indexing** in the UI.
2. Backend starts an async indexing job and returns a job id.
3. UI polls job status until done.
4. Ingestion pipeline compares files to `index-files.md`, extracts/chunks only new or changed files, creates embeddings, and updates OpenSearch. Failed files are logged in the same file.
5. User asks questions in chat UI.
6. Backend retrieves top-k relevant chunks from OpenSearch and sends context to `gpt-oss:20b`.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for OpenSearch)
- Ollama running local server on `http://127.0.0.1:1234/v1`
- Docling + EasyOCR dependencies (installed via `backend/requirements.txt`)

## 1. Start OpenSearch

```bash
docker compose up -d
```

Open http://localhost:5601

## 2. Configure Models

### Chat model
Use `gpt-oss:20b`.

### Embedding model (required)
Use `nomic-embed-text-v1.5` (GGUF `Q8_0` or `Q6_K`).

## 3. Run backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 4. Run frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Open `http://localhost:5173/#/`.

## API endpoints

- `POST /api/index/start` -> starts async indexing, returns `job_id`
- `GET /api/index/status/{job_id}` -> indexing status/progress
- `POST /api/chat` -> asks a question and returns answer + sources
- `GET /api/health` -> health check

## Notes

- If chat returns little/no context, run indexing again after adding files.
- OCR quality depends on source quality and EasyOCR language support.
- EasyOCR may download model files on the first run.