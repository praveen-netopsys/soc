# Explain Studio

A clean, modular platform for generating educational animated videos from raw text, presentations, and images.

---

## Architecture & Deployment Model

- **Frontend:** React 18 + Vite + TypeScript (runs on Windows laptop during development).
- **Backend:** FastAPI + Python 3.10+ (runs on Windows laptop during dev; intended for Ubuntu server).
- **AI Inference:** Ollama instance on company Ubuntu server hosting `qwen2.5:14b` and `qwen2.5vl:7b`.
- **Connectivity:** Secure loopback SSH port forwarding (`127.0.0.1:11434` / `127.0.0.1:8000`). No public port exposure.
- **Job Persistence:** Persistent SQLite database with transactional state tracking and job recovery across restarts.
- **Content Extraction:** Streaming multipart uploads, SHA-256 asset verification, `python-pptx` structured slide extraction, Pillow image metadata, and universal manifest storage.

---

## Completed Milestones

### Milestone 1: Clean, Minimalist Frontend
- Focused single-page workflow: source input, essential settings, generation progress, video preview.
- Live backend connection health monitoring.

### Milestone 2: Persistent Job Management & Multi-File Uploads
- Real streaming file uploads for TXT, MD, PPTX, PNG, JPG, JPEG, and WEBP.
- File-type and file-size validation with path-traversal prevention.
- Persistent SQLite tracking for jobs (`pending`, `extracting`, `ready_for_ai`, `processing`, `completed`, `failed`).

### Milestone 3: Content Extraction Engine
- Robust text parser with UTF-8 / UTF-8-BOM handling and heading detection.
- Structured PowerPoint extractor preserving slide order, shapes, tables, notes, and embedded media.
- Pillow image processor computing SHA-256 digests and dimensions.
- Universal Content Manifest preserving provenance at `storage/extracted/{job_id}/manifest.json`.
- Persistent single-worker background job consumer surviving restarts.

### Milestone 4: Remote AI Integration Foundation
- Reusable Ollama client reading base URL from `EXPLAIN_STUDIO_OLLAMA_BASE_URL`.
- Connection health check endpoint: `GET /v1/ai/health`.
- Model discovery and verification endpoint: `GET /v1/ai/models` (verifying `qwen2.5:14b` and `qwen2.5vl:7b`).
- Explicit safety guards preventing unauthorized automatic model downloads (`ollama pull`).
- UI status badge in frontend header reflecting Ollama tunnel status and available models.
- Comprehensive Windows SSH port-forwarding guide (`docs/ssh-tunnel-setup.md`).

### Milestone 5: Real AI Content Understanding
- **Text Reasoning with Qwen2.5:14B**: Extracts title, academic subject, comprehensive summary, main & supporting concepts, facts, formal definitions, concept relationships, concrete examples, prerequisites, Bloom's-aligned learning objectives, and ambiguity/gap detection.
- **Visual Understanding with Qwen2.5-VL:7B**: Analyzes uploaded and presentation-embedded images, diagrams, and charts, outputting visual type, objective description, visible text/labels, educational takeaways, and relevance to topic.
- **Source Grounding**: Every extracted fact, concept, and visual insight is traceable via `source_id`, `original_filename`, `slide_number`, and `content_block_id`.
- **Controlled Chunking & Hierarchical Summarization**: Large documents exceeding context limits are chunked by block boundaries, analyzed in sections, and synthesized without discarding content.
- **GPU Safety Guard**: Thread-safe single-task GPU lock ensuring zero concurrent model execution on the shared RTX 3090, automatic request queueing, and strict timeout enforcement.
- **Explicit Endpoints**: `POST /v1/jobs/{job_id}/analyze` (explicit trigger) and `GET /v1/jobs/{job_id}/analysis` (persisted structured JSON).
- **Interactive Frontend Analysis View**: Tabbed breakdown covering Overview, Concepts & Facts, Visual Analysis, and Source Grounding.

### Sprint 2: Complete AI Educational Director
- **Educational Planner (`app.ai.director.planner`)**: Produces pedagogical blueprints adapted to `beginner`, `intermediate`, or `advanced` audiences. Generates modular curriculum units with Bloom's taxonomy objectives, cognitive scaffolding, prerequisite bridges, and relatable real-world analogies.
- **Narration Script Generator (`app.ai.director.scriptwriter`)**: Authors spoken English scene-specific scripts with expressive tone markers, exact word counts, educational cadence calibrations, and verbatim content grounding.
- **Executable Storyboard Generator (`app.ai.director.storyboarder`)**: Generates structured, rendering-ready scene descriptions specifically targeted for Motion Canvas and Manim engines (`motion_canvas_code`, `manim_math`, `kinetic_typography`, `diagram_reveal`, `concept_map`, `comparison`, `table_breakdown`), complete with on-screen text elements, transitions, and durations.
- **Adaptive Duration Planner (`app.ai.director.duration`)**: Accurately computes realistic video duration factoring in educational speaking rates (125-145 WPM based on audience), cognitive pauses on complex mechanisms, viewer reading time for on-screen equations, and scene transition padding.
- **Programmatic QA & Anti-Hallucination Verification (`app.ai.director.qa`)**: Validates all citations against the real `manifest.json`. Zero-tolerance detection of hallucinated `content_block_id`s, calculating source coverage %, flagging unsupported claims, and certifying script/storyboard consistency.
- **Master Orchestration Pipeline (`app.ai.director.pipeline`)**: Automatically manages end-to-end execution and writes isolated artifacts: `plan.json`, `script.json`, `storyboard.json`, `duration.json`, `quality.json`, `director.json`.
- **New API Endpoints**:
  - `GET /v1/jobs/{job_id}/script` -> Returns `JobScriptResponse` with full narration breakdown.
  - `GET /v1/jobs/{job_id}/storyboard` -> Returns `JobStoryboardResponse` with executable scenes and duration.
  - `GET /v1/jobs/{job_id}/analysis` -> Enhanced with teaching plan, duration estimate, and QA scorecard.
- **Comprehensive Frontend Director Workspace**: Modern tabbed interface with Overview & Plan, Concepts & Facts, Narration Script, Storyboard, Visual Analysis, and QA & Source Grounding.
- **60 Automated Unit & Integration Tests**: Complete mocked coverage of all director components, edge cases, fallbacks, and API contracts.

---

## Quick Start (Windows Development)

### 1. SSH Tunnel Setup (for Remote AI Server)
See [`docs/ssh-tunnel-setup.md`](docs/ssh-tunnel-setup.md) for full instructions.

In PowerShell:
```powershell
ssh -N -L 11434:127.0.0.1:11434 <USER>@<SERVER_HOST>
```

### 2. Backend Setup & Startup
Requirements: Python 3.10+

```powershell
cd explain-studio\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Run test suite (60 automated tests):
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### 3. Frontend Setup & Startup
Requirements: Node.js 18+

```powershell
cd explain-studio\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` in your browser.

---

## Safety Commitments
- **No public exposure**: Ollama and FastAPI remain bound to loopback `127.0.0.1`.
- **No driver changes**: Zero modifications to NVIDIA drivers or host CUDA runtime.
- **No automatic model pulling**: No calls to `ollama pull` or automatic weight downloads.
- **No unauthorized GPU overload**: Model inference requests will follow in subsequent milestones under controlled concurrency.
