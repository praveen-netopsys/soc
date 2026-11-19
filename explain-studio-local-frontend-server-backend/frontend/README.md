# Frontend

Run on Windows:

```powershell
Copy-Item .env.example .env.local
npm install
npm run dev
```

The dev server binds to `127.0.0.1:5173`. The backend API base defaults to `http://127.0.0.1:8000`, which can be an SSH-forwarded server backend.

The UI currently performs only a health check and constructs a local JSON request preview. It does not upload files or create jobs yet.
