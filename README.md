# DocQuery

DocQuery lets you upload a PDF or paste text and ask questions about a selected
document. Answers stream in with clickable source citations (including PDF page
numbers), Balanced/Detailed modes, follow-up questions, and saved conversation
history. Documents and conversations belong to the signed-in account.

**Stack:** Next.js frontend · FastAPI API · Celery worker with Valkey as the queue ·
Qdrant vector database · Gemini for embeddings and answers (via LangChain) ·
SQLite for chat history · Clerk for sign-in.

## How it works

```
Next.js frontend
        │
   FastAPI API  ── sends indexing jobs ──▶  Valkey  ──▶  Celery worker
        │                                                     │
        ├── searches ──────────────────▶  Qdrant  ◀── stores chunks + embeddings
        ├── generates answers ─────────▶  Gemini
        └── chats + job progress ──────▶  SQLite
```

- **Upload:** the API saves the file, records a job, and queues it in Valkey. The
  Celery worker reads the PDF or text, splits it into chunks, embeds each chunk with
  Gemini, and stores them in Qdrant, reporting progress as it goes. Jobs survive a
  worker crash and are processed one at a time.
- **Ask:** follow-up questions are rewritten into standalone search queries, the
  closest passages from the selected document are retrieved, and Gemini streams an
  answer that cites them. Stop, retry, and saved history are handled by the API.

## 1. Prerequisites — install before cloning

- **Git**
- **Python 3.12+**
- **Node.js 24.x** and **pnpm** (for the frontend): `npm install --global pnpm@12.14.0`
- **Docker Desktop** — on Windows, enable WSL 2 and hardware virtualization if
  prompted, and use Linux containers.

You also need:

- **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey).
  Model availability and quotas depend on your account.
- **Clerk application:** create a development application in the
  [Clerk Dashboard](https://dashboard.clerk.com/), enable your preferred sign-in
  methods, and copy its publishable key, secret key, and Frontend API URL. Use the
  same application for the frontend and the API.

Docker runs Qdrant and Valkey locally; they need no accounts or keys.

## 2. Clone and install dependencies

The commands below use PowerShell.

```powershell
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git DocQuery
cd DocQuery
pnpm --dir client install --frozen-lockfile --ignore-scripts

cd server
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..
```

If PowerShell blocks `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

## 3. Configure environment files

Create `client/.env.local` with your Clerk keys:

```dotenv
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_replace_with_your_key
CLERK_SECRET_KEY=sk_test_replace_with_your_key
```

Create the API's environment file from the template:

```powershell
Copy-Item server/.env.example server/.env
```

Edit `server/.env` and replace the placeholders:

```dotenv
GEMINI_API_KEY=replace_with_your_gemini_key
CLERK_SECRET_KEY=sk_test_replace_with_your_key
CLERK_ISSUER=https://your-instance.clerk.accounts.dev
CLIENT_ORIGINS=http://localhost:3000
```

`CLERK_ISSUER` must be your Clerk application's exact Frontend API URL, including
`https://` and without a trailing slash.

The chat model defaults to `gemini-3.5-flash-lite`; override it with
`GEMINI_CHAT_MODEL=your_available_model`. Embeddings use `gemini-embedding-2`;
changing the embedding model requires re-indexing your documents.

Never commit real keys. Restart the API and worker after changing `server/.env`.

## 4. Start the app

### Quick start — one command

Start Docker Desktop, then from the `DocQuery` folder run:

```powershell
.\start.ps1
```

It starts Valkey and Qdrant, then runs the API, the worker and the frontend in one
terminal, with each log line labelled `api`, `worker` or `web`. Open
[http://localhost:3000](http://localhost:3000) once `web` reports it is ready.
Press `Ctrl+C` to stop all three. The processes are defined in `Procfile` and run
by [honcho](https://honcho.readthedocs.io/).

### Or run each part in its own terminal

Start Docker Desktop first, then open **three terminals** in the `DocQuery` folder.

### Terminal 1 — Docker services and API

```powershell
docker compose up -d
cd server
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --port 8000 --reload
```

Wait for `Application startup complete.` The health check is
[http://localhost:8000](http://localhost:8000).

### Terminal 2 — indexing worker

```powershell
cd server
.\.venv\Scripts\Activate.ps1
celery -A app.tasks worker --pool=solo --loglevel=info
```

Wait for `PDF worker ready`. Uploads are queued until the worker picks them up.
On Windows, `--pool=solo` is required.

### Terminal 3 — frontend

```powershell
pnpm --dir client dev
```

Open **[http://localhost:3000](http://localhost:3000)** and sign in. Keep port 3000
free so the configured origin matches. The API uses port 8000, Qdrant 6333, and
Valkey 6379.

Upload a small PDF with selectable text (or paste a note), wait for indexing to
finish, select the document, and ask a question. Image-only scans need OCR first.

## 5. Stop and restart

Press `Ctrl+C` in each terminal, then stop the containers:

```powershell
docker compose stop
```

Data persists in Docker's named volumes, `server/uploads/`, and
`server/data/conversations.sqlite` (created automatically). Avoid
`docker compose down -v` unless you intend to delete the Qdrant and Valkey data.
The Compose project name `pdf-rag-code` is intentional and keeps existing volumes.

## Tests

```powershell
cd server
.\.venv\Scripts\Activate.ps1
pytest

cd ..
pnpm --dir client test
pnpm --dir client lint
```

The backend tests use synthetic data, a signed test key for Clerk tokens, and
in-memory stand-ins for Gemini and Qdrant, so they need no network access, keys,
or running services. They cover uploads and indexing, document listing and
deletion, retrieval and citations, streaming, stop/retry, conversation history,
and cross-account isolation on every route.

## Project structure

```text
DocQuery/
├── client/                      Next.js / React frontend
│   ├── app/                     Pages and components (upload, library, chat, citations)
│   ├── lib/                     Authenticated fetch, stream reader, citation parsing
│   ├── tests/                   Frontend tests
│   └── middleware.js            Clerk middleware
├── server/                      Python backend
│   ├── app/
│   │   ├── main.py              FastAPI app and routes
│   │   ├── auth.py              Clerk session verification
│   │   ├── errors.py            JSON error format and CORS-safe error handling
│   │   ├── settings.py          Paths, service URLs and keys
│   │   ├── tasks.py             Celery app and indexing task
│   │   ├── ingestion.py         Read, chunk, embed and store documents
│   │   ├── jobs.py              Upload job records and failure messages
│   │   ├── ai.py                Gemini embeddings and Qdrant (REST)
│   │   ├── documents.py         Document listing and safe deletion
│   │   ├── chat.py              Retrieval, prompts, citations, query rewriting
│   │   ├── chat_service.py      One answer per chat: streaming, stop, retry
│   │   └── conversations.py     SQLite chat history
│   ├── tests/                   pytest suite
│   ├── uploads/                 Uploaded PDFs (not committed)
│   ├── data/                    SQLite database (not committed)
│   ├── requirements.txt
│   └── .env.example
├── docker-compose.yml           Qdrant and Valkey
├── Procfile                     API, worker and frontend processes for start.ps1
├── start.ps1                    Starts everything with one command
└── README.md
```

The current setup is for local use.
