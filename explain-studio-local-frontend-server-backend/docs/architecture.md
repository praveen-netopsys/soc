# Architecture decisions

## Deployment split

- React/Vite frontend runs only on the developer laptop during development.
- FastAPI backend is intended to run on the Ubuntu company server.
- Ollama remains on the company server and keeps its existing loopback binding.
- The local frontend reaches the backend using an SSH local port-forward.
- The backend reaches Ollama at `http://127.0.0.1:11434` from the server itself.

## First milestone limits

The starter only checks backend health and displays a local generation-request preview. It does not upload or persist files, run AI models, schedule GPU work, or render video.

## Later production direction

Add authentication, validated uploads, persistent job storage, a durable queue, source provenance, GPU scheduling, separate inference/render workers, TTS, animation rendering and final media validation incrementally.
