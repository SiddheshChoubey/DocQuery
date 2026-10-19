# DocQuery

DocQuery lets you upload a PDF or paste text and ask questions about a selected
document. It uses Gemini for embeddings and streamed answers, Qdrant for passage
retrieval, and Clerk for sign-in. Documents and saved conversations belong to the
signed-in account. Answers include source citations and Balanced/Detailed modes.

## 1. Prerequisites — install before cloning

- **Git:** 
- **Node.js 24.x:**
- **pnpm:** With Node installed, run `npm install --global pnpm@12.14.0`.
- **Docker Desktop:** 
  On Windows, enable WSL 2 and hardware
  virtualization if prompted. Use Linux containers.



You also need these accounts and credentials:

- **Gemini API key:** Create one in [Google AI Studio](https://aistudio.google.com/apikey).
  See Google's [API key guide](https://ai.google.dev/gemini-api/docs/api-key).
  Model availability and API quotas depend on your account.
- **Clerk application:** Create a development application in the
  [Clerk Dashboard](https://dashboard.clerk.com/), enable your preferred sign-in
  methods, and copy its publishable key, secret key, and Frontend API URL from
  the API keys page. Use this same application for both client and server.
  See [Clerk environment variables](https://clerk.com/docs/guides/development/clerk-environment-variables).

You do not need Python, OpenAI credentials, a Qdrant Cloud account, or a separate
SQLite installation. Docker runs Qdrant and Valkey locally without database keys.

## 2. Clone and install dependencies

The commands below use PowerShell. Replace the repository URL with your actual URL.

```powershell
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git DocQuery
cd DocQuery
pnpm --dir client install --frozen-lockfile --ignore-scripts
pnpm --dir server install --frozen-lockfile --ignore-scripts
New-Item -ItemType Directory -Force server/uploads | Out-Null
```

Install client and server dependencies separately; there is no root package.json.
The upload folder is required by the API and is excluded from Git.

## 3. Configure environment files

Create `client/.env.local` with the keys from your Clerk development application:

```dotenv
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_replace_with_your_key
CLERK_SECRET_KEY=sk_test_replace_with_your_key
```

Create the server environment file from the included template:

```powershell
Copy-Item server/.env.example server/.env
```

Edit `server/.env` and replace all placeholders:

```dotenv
GEMINI_API_KEY=replace_with_your_gemini_key
CLERK_SECRET_KEY=sk_test_replace_with_your_key
CLERK_ISSUER=https://your-instance.clerk.accounts.dev
CLIENT_ORIGINS=http://localhost:3000
```

Set `CLERK_ISSUER` to your Clerk application's exact Frontend API URL, including
`https://` and without an added trailing slash. It must match the session token's
issuer. Do not use the Clerk dashboard URL or your localhost URL here.

The code defaults to `gemini-3.5-flash-lite` for chat and `gemini-embedding-2` for
embeddings (`server/ai.js`). Confirm your Gemini account can use these models.
You can override the chat model by adding `GEMINI_CHAT_MODEL=your_available_model`
to `server/.env`. The embedding model is configured in code; changing it requires
a compatible collection and reindexing documents.

Keep real keys in these ignored environment files. Never commit them. Restart
the affected processes after changing environment variables.

## 4. Start the app

Start Docker Desktop first. Open **three terminals**, each in the cloned DocQuery
directory, and keep them running.

### Terminal 1 — Docker services and API

```powershell
docker compose up -d
docker compose ps
pnpm --dir server dev
```

Qdrant and Valkey should be running. The API prints
`Server started on PORT:8000`. Its public health endpoint is
[http://localhost:8000](http://localhost:8000).

### Terminal 2 — indexing worker

```powershell
pnpm --dir server dev:worker
```

Wait for `PDF worker ready`. The API queues uploads; this separate worker reads,
splits, embeds, and indexes them. Uploads cannot finish indexing without it.

### Terminal 3 — frontend

```powershell
pnpm --dir client dev
```

Open **[http://localhost:3000](http://localhost:3000)** and sign in. Keep port 3000
available; if Next.js switches to another port, free port 3000 and restart it so
the configured origin matches. The API uses port 8000, Qdrant 6333, and Valkey 6379.

Upload a small PDF containing selectable text or paste a short note. Wait for
indexing to finish, select the document, and ask a question about its contents.
Try a follow-up question, inspect citations, and reload to check saved history.
Image-only scans are not supported through OCR.

## 5. Stop and restart

Press `Ctrl+C` in each application terminal, then stop the database containers:

```powershell
docker compose stop
```

For later sessions, repeat the three-terminal startup sequence. Installation and
environment configuration are only needed once unless dependencies or settings change.

Data persists in Docker's named volumes, `server/uploads/`, and
`server/data/conversations.sqlite`. SQLite is created automatically. Avoid
`docker compose down -v` unless you intend to delete the Docker database volumes.
The internal Compose name `pdf-rag-code` is intentional and preserves existing
volume names; the application is named DocQuery.

## Tests and optional production frontend

Run the automated tests from the repository root:

```powershell
pnpm --dir client test
pnpm --dir server test
```

Normal tests use synthetic data and mocked model calls. The real Qdrant integration
test is opt-in and skipped unless `QDRANT_TEST_URL` is set.

To run the frontend in production mode locally, stop its development process first:

```powershell
pnpm --dir client build
pnpm --dir client start
```

Keep Docker, the API, and the worker running as before. A production frontend build
is optional for local development and does not deploy the application.


## Project structure

```text
DocQuery/
├── client/                         Next.js / React / TypeScript frontend
│   ├── app/
│   │   ├── layout.tsx              Clerk provider and signed-out welcome screen
│   │   ├── page.tsx                Signed-in workspace and document selection
│   │   ├── globals.css             Application styles
│   │   └── components/
│   │       ├── file-upload.tsx     PDF/text upload and indexing progress
│   │       ├── document-library.tsx  Document list, selection, and deletion UI
│   │       ├── chat.tsx            Messages, history, answer mode, and chat controls
│   │       └── cited-answer.tsx    Markdown answers with source citations
│   ├── components/ui/              Reusable button and input components
│   ├── lib/
│   │   ├── authenticated-fetch.ts  Adds Clerk session tokens to API requests
│   │   ├── chat-stream.ts         Reads streamed chat events from the API
│   │   └── remark-citations.ts    Processes citation markers in Markdown
│   ├── public/                     Static assets
│   ├── tests/                      Frontend component and helper tests
│   ├── middleware.ts               Clerk middleware
│   ├── package.json                Frontend dependencies and scripts
│   └── .env.local                  Local Clerk configuration (not committed)
├── server/                         Express API and background indexing worker
│   ├── index.js                    Starts the API and connects its services
│   ├── app.js                      Upload, document, and health routes
│   ├── auth.js                     Verifies Clerk tokens for data requests
│   ├── worker.js                   Processes queued uploads and stores vectors
│   ├── ingestion.js                Reads inputs and splits text into chunks
│   ├── ai.js                       Gemini models, embeddings, and Qdrant config
│   ├── documents.js                Lists documents and builds retrieval filters
│   ├── delete-document.js          Removes a document's vectors, files, and jobs
│   ├── chat-routes.js              Conversation and streaming HTTP routes
│   ├── chat-service.js             Coordinates history, retrieval, and generation
│   ├── query-rewrite.js            Rewrites follow-up questions using chat context
│   ├── citations.js                Answer prompts, streaming, and source mapping
│   ├── conversations.js            Stores account-scoped chats in SQLite
│   ├── upload-status.js            Converts indexing failures into status messages
│   ├── tests/                      Backend and optional Qdrant integration tests
│   ├── uploads/                    Local uploaded PDFs
│   ├── data/                       Generated SQLite history
│   ├── package.json                API/worker dependencies and scripts
│   └── .env                        Local Gemini/Clerk configuration
├── docker-compose.yml              Local Qdrant and Valkey services and volumes
└── README.md                       Installation and local usage guide
```

Create `server/uploads/` during setup; `server/data/` is created automatically when the API initializes conversation storage.

The current setup is for local use.
