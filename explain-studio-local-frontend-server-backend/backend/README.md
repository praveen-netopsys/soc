# Backend foundation

This is a minimal FastAPI service. It exposes only `GET /health` and `GET /v1/info`.

It does not upload files, call Ollama, load models, or use the GPU.

Run locally on Windows for a smoke test:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Intended Ubuntu launch command after deployment approval:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
