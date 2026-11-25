api: cd server && .venv\Scripts\python -u -m uvicorn app.main:app --port 8000 --reload
worker: cd server && .venv\Scripts\python -u -m celery -A app.tasks worker --pool=solo --loglevel=info
web: pnpm --dir client dev
