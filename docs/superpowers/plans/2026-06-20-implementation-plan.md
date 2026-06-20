# 中转站模型质量检测平台 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a complete model quality detection platform supporting OpenAI, Anthropic, and Gemini protocols with real-time testing, comprehensive scoring, and PDF report generation.

**Architecture:** 
- **Backend**: FastAPI async server with SQLite database, provider adapters for three protocols, probe engine with strategy-based execution, signal-weighted authenticity scoring
- **Frontend**: Vue 3 SPA with Naive UI components, real-time SSE progress, ECharts visualizations, PDF export workflow  
- **Data Layer**: Three-tier results model (detection_result → strategy_result → probe_record) enabling root-cause traceability

**Tech Stack:**
- Backend: Python 3.11+, FastAPI, SQLAlchemy ORM, aiohttp, cryptography, weasyprint, pydantic
- Frontend: Vue 3, Vite, Naive UI, ECharts, axios
- Database: SQLite with schema migrations
- Testing: pytest (backend), fixtures for HTTP mocking, zero-network testing

## Global Constraints

- **SQLite column comments**: Use inline DDL comments (SQLite lacks native COMMENT syntax); maintain parallel data dictionary in `doc/` directory
- **API Key handling**: Never log, return, or echo keys; encrypt storage with local master key; all responses return `has_api_key: bool` only
- **Database schema**: Integer autoincrement primary keys (no string PKs), no foreign key constraints, `datetime` timestamps only, TEXT for decimal prices, all fields documented
- **Protocols**: Support OpenAI, Anthropic, Gemini native (Developer + Vertex styles) and Gemini OpenAI-compat layer; use protocol set (JSON) not enum
- **Error deidentification**: Regex-scrub Authorization/keys from all error messages, logs, probe records before storage
- **Gemini endpoint styles**: Conditionally support `gemini_developer` (v1beta) and `vertex` (v1/projects/...) with project/location/auth_style config at model level
- **Testing constraint**: Zero network—all HTTP mocking via fixtures; locally executable with `pytest` (no CI required, repo is private)
- **Documentation**: All changes update `doc/TODO.md` status markers; license format in README as `MIT © 2026 <author>`

---

## File Structure (Abbreviated)

**Backend**: `backend/app/` with modules: `main.py`, `config.py`, `models/` (database.py, schemas.py), `security/` (crypto.py, sanitizer.py), `database/` (session.py, migrations.py), `providers/` (base.py, openai_adapter.py, anthropic_adapter.py, gemini_adapter.py, adapter_factory.py), `probes/` (base.py, registry.py, connectivity.py, performance.py, billing.py, capability.py, authenticity.py, gemini_features.py), `engine/` (executor.py, scheduler.py), `scoring/` (signal.py, authenticity_scorer.py, aggregator.py, confidence.py), `api/` (stations.py, models.py, tasks.py, reports.py, router.py), `services/`, `templates/`, `utils/`, `tests/` with mirrors

**Frontend**: `frontend/src/` with `main.js`, `router/`, `components/`, `api/`, `stores/`, `assets/`

---

## Phase 1: Backend Infrastructure (Tasks 1–5)

### Task 1: Backend Project Initialization

**Files:**
- Create: `backend/requirements.txt`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/config.py`, `.env.example`
- Create: `backend/tests/__init__.py`, `backend/tests/conftest.py`

**Interfaces:**
- Produces: Runnable FastAPI app on `http://localhost:8000`; `pytest` executable

- [ ] **Step 1.1: Create backend/requirements.txt**

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic==2.5.0
pydantic-settings==2.1.0
sqlalchemy==2.0.23
alembic==1.12.1
aiohttp==3.9.1
httpx==0.25.2
cryptography==41.0.7
python-jose==3.3.0
weasyprint==60.0
jinja2==3.1.2
tiktoken==0.5.2
python-dotenv==1.0.0
tenacity==8.2.3
pytest==7.4.3
pytest-asyncio==0.21.1
pytest-cov==4.1.0
freezegun==1.4.0
```

- [ ] **Step 1.2: Install dependencies**

```bash
cd backend && pip install -r requirements.txt
```

Expected: All packages resolve.

- [ ] **Step 1.3: Create app/config.py**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    debug: bool = False
    host: str = "localhost"
    port: int = 8000
    database_url: str = "sqlite:///./app.db"
    api_key_master_key: str = ""
    cors_origins: list = ["http://localhost:5173"]
    max_concurrent_tasks: int = 2
    default_max_concurrency_per_task: int = 2
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
```

- [ ] **Step 1.4: Create app/main.py**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

app = FastAPI(
    title="Model Quality Detection Platform",
    version="0.1.0",
    debug=settings.debug
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

- [ ] **Step 1.5: Create .env.example**

```
DEBUG=false
DATABASE_URL=sqlite:///./app.db
API_KEY_MASTER_KEY=
CORS_ORIGINS=http://localhost:5173
```

- [ ] **Step 1.6: Create tests/conftest.py**

```python
import pytest
from sqlalchemy import create_engine

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    yield engine
    engine.dispose()
```

- [ ] **Step 1.7: Test FastAPI runs**

```bash
cd backend && python -m uvicorn app.main:app --reload
# In another terminal: curl http://localhost:8000/health
```

Expected: `{"status": "ok"}`

- [ ] **Step 1.8: Commit**

```bash
git add backend/ .env.example && git commit -m "feat: bootstrap FastAPI project"
```

---

### Task 2: Database Schema & ORM

**Files:**
- Create: `backend/app/database/`, `backend/app/models/database.py`, `backend/app/models/schemas.py`, `doc/DATABASE_SCHEMA.md`
- Create: `tests/integration/test_database.py`

**Interfaces:**
- Produces: SQLAlchemy `Base`, ORM models, Pydantic schemas, `SessionLocal()` factory

- [ ] **Step 2.1: Create database/session.py**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2.2: Create models/database.py with full ORM**

(See detailed code in full plan file—covers RelayStation, Model, DetectionTask, DetectionResult, StrategyResult, ProbeRecord tables)

- [ ] **Step 2.3: Create models/schemas.py with Pydantic DTOs**

(Covers RelayStationCreate, ModelCreate, DetectionTaskCreate, DetectionResultResponse, etc.)

- [ ] **Step 2.4: Create database/migrations.py**

```python
from app.database.session import engine
from app.models.database import Base

def init_db():
    Base.metadata.create_all(bind=engine)
```

- [ ] **Step 2.5: Update app/main.py to call init_db on startup**

```python
from app.database.migrations import init_db

@app.on_event("startup")
async def startup():
    init_db()
```

- [ ] **Step 2.6: Create doc/DATABASE_SCHEMA.md**

(Data dictionary with all table columns, types, and comments)

- [ ] **Step 2.7: Create tests/integration/test_database.py**

```python
from app.database.migrations import init_db
from app.models.database import Base

def test_schema_created(test_db):
    Base.metadata.create_all(bind=test_db)
    from sqlalchemy import inspect
    tables = inspect(test_db).get_table_names()
    assert all(t in tables for t in ["relay_stations", "models", "detection_tasks", "detection_results", "strategy_results", "probe_records"])
```

- [ ] **Step 2.8: Run and commit**

```bash
cd backend && pytest tests/integration/test_database.py -v
git add backend/app/database/ backend/app/models/ doc/DATABASE_SCHEMA.md tests/integration/test_database.py
git commit -m "feat: define SQLAlchemy ORM and Pydantic schemas"
```

---

### Task 3: Security (Encryption & Sanitization)

**Files:**
- Create: `backend/app/security/crypto.py`, `backend/app/security/sanitizer.py`, `tests/unit/test_security.py`

**Interfaces:**
- Produces: `KeyManager`, `ErrorSanitizer` classes

- [ ] **Step 3.1–3.8:** (Follow detailed steps in full plan file for key encryption, error regex patterns, unit tests)

- [ ] **Step 3.9: Commit**

```bash
git add backend/app/security/ tests/unit/test_security.py
git commit -m "feat: implement API key encryption and error deidentification"
```

---

### Task 4: Provider Adapter Framework

**Files:**
- Create: `backend/app/providers/base.py`, `backend/app/providers/adapter_factory.py`, `tests/unit/test_providers_base.py`

**Interfaces:**
- Produces: `ProviderAdapter` abstract base, `ProbeContext`, `ProbeResponse`, `AdapterFactory`

- [ ] **Step 4.1–4.5:** (Define base classes, factory, unit tests per full plan)

- [ ] **Step 4.6: Commit**

```bash
git add backend/app/providers/base.py backend/app/providers/adapter_factory.py tests/unit/test_providers_base.py
git commit -m "feat: define ProviderAdapter abstraction and factory"
```

---

### Task 5: HTTP Client Utility

**Files:**
- Create: `backend/app/utils/http_client.py`, `tests/unit/test_http_client.py`

**Interfaces:**
- Produces: `HTTPClient` with exponential backoff retry, deidentification, async context manager

- [ ] **Step 5.1–5.4:** (Async HTTP client with tenacity retry, error deidentification per full plan)

- [ ] **Step 5.5: Commit**

```bash
git add backend/app/utils/http_client.py tests/unit/test_http_client.py
git commit -m "feat: add async HTTP client with retry and sanitization"
```

---

## Phase 2: Provider Adapters (Tasks 6–8)

### Task 6: OpenAI Provider Adapter

- Create: `backend/app/providers/openai_adapter.py`, `tests/fixtures/openai_responses.py`, `tests/unit/test_openai_adapter.py`
- Implements: `/v1/chat/completions`, `/v1/models` endpoints, streaming support, usage parsing
- Register: `("openai", "native")`

- [ ] **Step 6.1–6.5:** (Full adapter implementation with streaming, TTFT measurement, token counting per full plan)

- [ ] **Step 6.6: Commit**

```bash
git add backend/app/providers/openai_adapter.py tests/fixtures/openai_responses.py tests/unit/test_openai_adapter.py
git commit -m "feat: implement OpenAI provider adapter"
```

---

### Task 7: Anthropic Provider Adapter

- Create: `backend/app/providers/anthropic_adapter.py`, `tests/fixtures/anthropic_responses.py`, `tests/unit/test_anthropic_adapter.py`
- Implements: `/v1/messages`, streaming with usage fallback estimation, model fetch
- Register: `("anthropic", "native")`

- [ ] **Step 7.1–7.5:** (Per OpenAI pattern, adapted for Anthropic API)

- [ ] **Step 7.6: Commit**

```bash
git add backend/app/providers/anthropic_adapter.py tests/fixtures/anthropic_responses.py tests/unit/test_anthropic_adapter.py
git commit -m "feat: implement Anthropic provider adapter"
```

---

### Task 8: Gemini Provider Adapter (Three Paths)

- Create: `backend/app/providers/gemini_adapter.py`, `tests/fixtures/gemini_responses.py`, `tests/unit/test_gemini_adapter.py`
- Implements: 
  - `gemini_developer` path: `v1beta/:generateContent`
  - `vertex` path: `v1/projects/{project}/locations/{location}/models/{model}:generateContent`
  - `openai_compat` path: `/v1/chat/completions`
- Extracts: `usageMetadata`, `thoughtsTokenCount`, `safetyRatings`, `groundingMetadata`, `executableCode` for fingerprints
- Register: `("gemini", "native")`, `("gemini", "openai_compat")`

- [ ] **Step 8.1–8.10:** (Conditional endpoint routing, triple-response parsing, :countTokens integration per full plan)

- [ ] **Step 8.11: Commit**

```bash
git add backend/app/providers/gemini_adapter.py tests/fixtures/gemini_responses.py tests/unit/test_gemini_adapter.py
git commit -m "feat: implement Gemini provider adapter with native + vertex + compat paths"
```

---

## Phase 3: Probe Framework & Core Probes (Tasks 9–14)

### Task 9: Probe Base Framework & Registry

- Create: `backend/app/probes/base.py`, `backend/app/probes/registry.py`, `tests/unit/test_probes_base.py`
- Produces: `Probe` abstract, `ProbeStatus` enum, `ProbeResult`, `ProbeRegistry`

- [ ] **Step 9.1–9.6:** (Base class definitions, registry with category grouping, unit tests per full plan)

- [ ] **Step 9.7: Commit**

```bash
git add backend/app/probes/base.py backend/app/probes/registry.py tests/unit/test_probes_base.py
git commit -m "feat: define Probe abstraction and registration system"
```

---

### Task 10: Connectivity & Performance Probes

- Create: `backend/app/probes/connectivity.py`, `backend/app/probes/performance.py`, `tests/unit/test_connectivity_performance_probes.py`
- Implements:
  - `connectivity`: HTTP 200 + response structure validation
  - `ttft`: Time to first token (streaming)
  - `throughput`: Tokens per second
  - `stability`: Success rate + p95 latency (N repeats)
- Register all four probes with weights

- [ ] **Step 10.1–10.10:** (TTFT measurement on stream chunks, exponential backoff on repeat failures, metrics aggregation per full plan)

- [ ] **Step 10.11: Commit**

```bash
git add backend/app/probes/connectivity.py backend/app/probes/performance.py tests/unit/test_connectivity_performance_probes.py
git commit -m "feat: implement connectivity and performance probes"
```

---

### Task 11: Billing & Capability Probes

- Create: `backend/app/probes/billing.py`, `backend/app/probes/capability.py`, `tests/unit/test_billing_capability_probes.py`
- Implements:
  - `billing_consistency`: Local tokenizer vs. declared usage deviation check
  - `cap_context_length`: Recursive binary search to determine actual limit
  - `cap_streaming`: Check SSE support
  - `cap_function_call`: Validate tool/function call structure
  - `cap_multimodal`: Image/video input acceptance
  - `cap_json_mode`: Structured output validation
- Register all, with `applicable()` filtering by declared capabilities

- [ ] **Step 11.1–11.12:** (Recursive context search with configurable step size, JSON schema validation, multi-modal fixture mocks per full plan)

- [ ] **Step 11.13: Commit**

```bash
git add backend/app/probes/billing.py backend/app/probes/capability.py tests/unit/test_billing_capability_probes.py
git commit -m "feat: implement billing and capability probes with adaptive testing"
```

---

### Task 12: Authenticity Probes (Shell & Reversal Features)

- Create: `backend/app/probes/authenticity.py`, `tests/unit/test_authenticity_probes.py`
- Implements:
  - `shell_usage_missing`: Detect absent/malformed usage fields
  - `shell_special_field_absent`: Check for protocol-specific fields (`safetyRatings`, `system_fingerprint`, etc.)
  - `shell_tokenizer_mismatch`: Compare local vs. declared token counts
  - `shell_capability_gap`: Compare declared vs. actual probe pass rates
  - `reverse_shell_artifact`: Regex-detect injected system instructions
  - `reverse_version_anomaly`: Validate `modelVersion`/`system_fingerprint` presence and format
  - `reverse_ratelimit_pattern`: Frequency analysis for subscription-pattern limits
  - `reverse_header_missing`: Check response headers for auth signatures
  - `reverse_studio_signature`: Detect free-tier quota patterns (Gemini)

- [ ] **Step 12.1–12.15:** (Feature extraction from probe records, regex patterns for shell/tool artifacts, per full plan)

- [ ] **Step 12.16: Commit**

```bash
git add backend/app/probes/authenticity.py tests/unit/test_authenticity_probes.py
git commit -m "feat: implement shell/reversal feature extraction probes"
```

---

### Task 13: Gemini Functional Fingerprint Probes (A & B Groups)

- Create: `backend/app/probes/gemini_features.py`, `tests/unit/test_gemini_features_probes.py`
- Implements (A Group, dual-directional):
  - `gemini_thinking`: Verify `thoughtsTokenCount` presence + correctness
  - `gemini_code_execution`: Validate `executableCode` structure
  - `gemini_url_context`: Check URL retrieval metadata
  - `gemini_search_grounding`: Validate `groundingMetadata.groundingChunks`
  - `gemini_json_mode`: Validate `responseMimeType="application/json"` enforcement
  - `gemini_caching`: Check `cachedContentTokenCount` > 0
  - `gemini_logprobs`: Validate `logprobsResult` structure
  - `gemini_safety_ratings`: Verify `safetyRatings` completeness
  - `gemini_token_consistency`: `:countTokens` vs. `usageMetadata` comparison
  - `gemini_multimodal_timestamp`: Video analysis with frame-level accuracy
  
- Implements (B Group, one-vote confirm):
  - `gemini_url_context_exclusive`: Developer-only (confirms real Gemini)
  - `gemini_vertex_rag`: Vertex-only (confirms real Gemini)
  - `gemini_maps_grounding`: Vertex 3 Enterprise only
  - `gemini_safety_severity`: Vertex-only safety severity method

- Constraint: `applicable=false` for `access_mode=openai_compat`

- [ ] **Step 13.1–13.20:** (A group bidirectional logic, B group one-ticket-confirms, confidence downgrade for compat, per full plan)

- [ ] **Step 13.21: Commit**

```bash
git add backend/app/probes/gemini_features.py tests/unit/test_gemini_features_probes.py
git commit -m "feat: implement Gemini functional fingerprint probes (A+B groups)"
```

---

### Task 14: Probe Error Handling & Boundary Conditions

- Create: `backend/app/probes/errors.py`, `tests/unit/test_probe_errors.py`
- Implements:
  - Nine error classes: `ConnectivityError`, `AuthError`, `QuotaExceeded`, `RateLimit`, `Timeout`, `Upstream5xx`, `ParseError`, `CapabilityUnsupported`, `BudgetExceeded`
  - Categorization logic: retry strategy, short-circuit triggers
  - Deidentification of raw excerpts
  - Retry decorator with exponential backoff + Retry-After respect
  - Edge case handlers: Anthropic streaming no-usage fallback, flow interruption detection, correct vs. error finish reasons

- [ ] **Step 14.1–14.12:** (Error enum, categorization matrix, retry orchestration per full plan)

- [ ] **Step 14.13: Commit**

```bash
git add backend/app/probes/errors.py tests/unit/test_probe_errors.py
git commit -m "feat: define probe-level error hierarchy and retry strategies"
```

---

## Phase 4: Detection Engine & Scoring (Tasks 15–20)

### Task 15: Task Executor & Concurrency Management

- Create: `backend/app/engine/executor.py`, `tests/integration/test_engine_executor.py`
- Implements:
  - Task lifecycle: `pending → running → (completed | failed | canceled)`
  - Connectivity short-circuit: failure → task marked "unavailable" → terminate
  - Probe sequencing: connectivity first, then category-parallel with concurrency limits
  - SSE event emission: `task.started`, `probe.completed`, `task.scored`, `task.completed`, `task.failed`
  - Cancellation via cancel token at probe boundaries
  - Timeout: per-request + per-task fallback
  - Progress calculation: `completed_probes / total_enabled_probes`

- [ ] **Step 15.1–15.15:** (Task state machine, cancel token propagation, SSE event batching, timeout handler per full plan)

- [ ] **Step 15.16: Commit**

```bash
git add backend/app/engine/executor.py tests/integration/test_engine_executor.py
git commit -m "feat: implement detection task executor with lifecycle management"
```

---

### Task 16: Async Task Queue & Global Scheduler

- Create: `backend/app/engine/scheduler.py`, `tests/integration/test_engine_scheduler.py`
- Implements:
  - Global task pool (limit: `max_concurrent_tasks` from settings)
  - Queue management: enqueue pending tasks, spawn executor on slot availability
  - Per-task concurrency limits: control within-task probe parallelism

- [ ] **Step 16.1–16.6:** (Asyncio-based queue, slot allocation, fair scheduling per full plan)

- [ ] **Step 16.7: Commit**

```bash
git add backend/app/engine/scheduler.py tests/integration/test_engine_scheduler.py
git commit -m "feat: implement global task queue with concurrency control"
```

---

### Task 17: Signal Model & Weighting

- Create: `backend/app/scoring/signal.py`, `tests/unit/test_signal_weighting.py`
- Implements:
  - `Signal` dataclass: key, target (`shell` | `direct`), direction (`confirm` | `refute`), severity (0–1), weight, confidence, evidence
  - `SignalAggregator`: Accumulate signals, apply weighting formula
  - Confirm logic: backfill (capped at 100), refute logic: deduction
  - Confidence adjustment: compat × 0.6, sample-count factors

- [ ] **Step 17.1–17.8:** (Signal model definition, weighting arithmetic, confidence rules per full plan)

- [ ] **Step 17.9: Commit**

```bash
git add backend/app/scoring/signal.py tests/unit/test_signal_weighting.py
git commit -m "feat: implement signal model and weighted aggregation"
```

---

### Task 18: Authenticity Scoring (Shell + Direct Sub-Scores)

- Create: `backend/app/scoring/authenticity_scorer.py`, `tests/unit/test_authenticity_scorer.py`
- Implements:
  - `shell_score`: Start 100, deduct for refute signals (usage missing, special fields absent, tokenizer mismatch, capability gap), confirm for Gemini A-group
  - `direct_score`: Start 100, deduct for reversal signals (shell artifacts, version anomaly, rate limit pattern, header missing, studio signature)
  - Gemini A-group double-direction: `supported` → strong confirm (shell_score floor), "claimed but unsupported" → refute
  - Gemini B-group one-ticket: `supported` → lock `shell_score ≥ H`
  - Compat path confidence downgrade: × 0.6

- [ ] **Step 18.1–18.10:** (Two-dimensional scoring with direction-aware contrib, Gemini special logic, per full plan)

- [ ] **Step 18.11: Commit**

```bash
git add backend/app/scoring/authenticity_scorer.py tests/unit/test_authenticity_scorer.py
git commit -m "feat: implement shell_score and direct_score computation"
```

---

### Task 19: Score Aggregation (Dimension + Overall)

- Create: `backend/app/scoring/aggregator.py`, `tests/unit/test_score_aggregation.py`
- Implements:
  - Dimension score: weighted average of passing strategies in that category (skip doesn't count, degraded partial, fail counts as 0)
  - Overall score: weighted average of five dimensions
  - Authenticity special: `min(shell_score, direct_score)` (short-board logic)
  - Confidence calc: base 1.0, factors for compat (-0.4), sample size, multi-signal support

- [ ] **Step 19.1–19.8:** (Weighted averaging, dimension aggregation, confidence synthesis per full plan)

- [ ] **Step 19.9: Commit**

```bash
git add backend/app/scoring/aggregator.py tests/unit/test_score_aggregation.py
git commit -m "feat: implement dimension and overall score aggregation"
```

---

### Task 20: Confidence Adjustment & Grading Logic

- Create: `backend/app/scoring/confidence.py`, `tests/unit/test_confidence_grading.py`
- Implements:
  - Confidence rules: compat (-0.4), sample coverage (N repeats), single-signal penalty
  - Grading thresholds: `正常 (≥H=75)`, `可能可疑 (45~75)`, `高度可疑 (<45)` (defaults, adjustable)
  - Confidence display: with sub-score and main score

- [ ] **Step 20.1–20.8:** (Confidence adjustment logic, threshold application, level assignment per full plan)

- [ ] **Step 20.9: Commit**

```bash
git add backend/app/scoring/confidence.py tests/unit/test_confidence_grading.py
git commit -m "feat: implement confidence adjustment and grading thresholds"
```

---

## Phase 5: API Layer & Services (Tasks 21–27)

### Task 21: Station CRUD & Model Fetch Orchestration

- Create: `backend/app/api/stations.py`, `backend/app/services/station_service.py`, `backend/app/services/model_service.py`, `tests/integration/test_station_api.py`
- Implements:
  - `POST /api/stations`: Create with protocols array, base_url, api_key (encrypted), name
  - `GET /api/stations`, `GET /api/stations/{id}`: List/fetch (api_key → `has_api_key: bool`)
  - `PUT /api/stations/{id}`, `DELETE /api/stations/{id}`: Update/delete
  - `POST /api/stations/{id}/models/fetch`: Multi-protocol parallel fetch + merge + dedup
  - `GET /api/stations/{id}/models`: List models for station
  - `POST /api/stations/{id}/models`: Manual model entry
  - Response always excludes key plaintext (security rule 3.5)

- [ ] **Step 21.1–21.15:** (API endpoints, model fetch orchestration per 10.6, Pydantic validation per full plan)

- [ ] **Step 21.16: Commit**

```bash
git add backend/app/api/stations.py backend/app/services/station_service.py backend/app/services/model_service.py tests/integration/test_station_api.py
git commit -m "feat: implement station CRUD and multi-protocol model fetch"
```

---

### Task 22: Detection Task Lifecycle API

- Create: `backend/app/api/tasks.py`, `backend/app/services/task_service.py`, `tests/integration/test_task_api.py`
- Implements:
  - `POST /api/tasks`: Create task, validate model ∈ station, return task_id, async spawn executor
  - `GET /api/tasks`, `GET /api/tasks/{id}`: List/fetch task status + progress
  - `POST /api/tasks/{id}/cancel`: Request cancellation (cancel token)
  - `GET /api/tasks/{id}/events`: SSE stream for real-time progress
  - Result storage: writes `detection_result`, `strategy_result`, `probe_record` after completion

- [ ] **Step 22.1–22.12:** (Task creation + validation, SSE streaming, async cleanup per full plan)

- [ ] **Step 22.13: Commit**

```bash
git add backend/app/api/tasks.py backend/app/services/task_service.py tests/integration/test_task_api.py
git commit -m "feat: implement task lifecycle and SSE progress streaming"
```

---

### Task 23: Result & Report API

- Create: `backend/app/api/reports.py`, `backend/app/services/report_service.py`, `tests/integration/test_report_api.py`
- Implements:
  - `GET /api/tasks/{id}/result`: Return `detection_result` with score breakdown + authenticity sub-scores
  - `GET /api/tasks/{id}/strategies`: List all `strategy_result` for the task (drillable to `probe_record`)
  - `POST /api/tasks/{id}/report.pdf`: Accept front-end chart base64 images, generate HTML → PDF via weasyprint
  - `GET /api/tasks/{id}/report.pdf` (fallback): Return pure-data PDF (no images)
  - All responses deidentified (no keys, deidentified error messages)

- [ ] **Step 23.1–23.10:** (Report assembly from three-tier results, deidentification, PDF generation per full plan)

- [ ] **Step 23.11: Commit**

```bash
git add backend/app/api/reports.py backend/app/services/report_service.py tests/integration/test_report_api.py
git commit -m "feat: implement result reporting and PDF export"
```

---

### Task 24: Main API Router & Error Handling Middleware

- Create: `backend/app/api/router.py`, `backend/app/api/exceptions.py`, `tests/integration/test_api_error_handling.py`
- Implements:
  - Router aggregation: include stations, models, tasks, reports blueprints
  - Global exception handlers: deidentify errors, return 400/401/403/500 with sanitized messages
  - Request validation: Pydantic coercion, reject invalid protocol/mode combos
  - Timestamp precision: milliseconds for TTFT, seconds for task times

- [ ] **Step 24.1–24.8:** (Router setup, exception middleware, request validation per full plan)

- [ ] **Step 24.9: Commit**

```bash
git add backend/app/api/router.py backend/app/api/exceptions.py tests/integration/test_api_error_handling.py
git commit -m "feat: integrate API endpoints and error handling"
```

---

### Task 25: PDF Template & Generation Service

- Create: `backend/app/templates/report.html`, `backend/app/services/pdf_service.py`, `tests/unit/test_pdf_generation.py`
- Implements:
  - Jinja2 HTML template: A4 layout, 9-block report structure (header, overview, authenticity, performance, billing, capability, Gemini fingerprints, completeness, appendix)
  - Inline chart images: base64 from frontend ECharts
  - Deidentified data: no keys, short field names, redacted error messages
  - weasyprint HTML→PDF conversion
  - Chinese font embedding (ensure CJK characters render)

- [ ] **Step 25.1–25.8:** (Template design, chart embedding, font config, weasyprint invocation per full plan)

- [ ] **Step 25.9: Commit**

```bash
git add backend/app/templates/report.html backend/app/services/pdf_service.py tests/unit/test_pdf_generation.py
git commit -m "feat: implement PDF report template and generation"
```

---

### Task 26: Tokenizer Utilities & Time Measurement

- Create: `backend/app/utils/tokenizers.py`, `backend/app/utils/time_utils.py`, `tests/unit/test_tokenizers_time_utils.py`
- Implements:
  - `count_tokens_openai(model, text)`: tiktoken lookup
  - `count_tokens_gemini_via_api(adapter, model, text)`: Call `:countTokens` endpoint
  - `estimate_tokens_anthropic(text)`: Fallback approximation
  - `measure_ttft_stream(chunks, timeout)`: First non-empty chunk latency
  - `measure_latency(start, end)`: Microsecond-precision timing

- [ ] **Step 26.1–26.8:** (Tokenizer integration per adapter, TTFT measurement logic per full plan)

- [ ] **Step 26.9: Commit**

```bash
git add backend/app/utils/tokenizers.py backend/app/utils/time_utils.py tests/unit/test_tokenizers_time_utils.py
git commit -m "feat: add tokenizer and timing utilities"
```

---

### Task 27: Integration Testing for API-to-Engine Pipeline

- Create: `tests/e2e/test_end_to_end_workflow.py`
- Implements:
  - Full flow: POST station → POST models/fetch → POST task → SSE stream → GET result → POST PDF
  - Mock HTTP calls via fixtures, no real network
  - Verify all database writes (task, results, strategies, probes)
  - Validate deidentification at each step
  - Check score computation (authenticity especially)

- [ ] **Step 27.1–27.6:** (E2E fixture setup, flow validation, deidentification audit per full plan)

- [ ] **Step 27.7: Commit**

```bash
git add tests/e2e/test_end_to_end_workflow.py
git commit -m "test: add end-to-end workflow validation"
```

---

## Phase 6: Frontend Implementation (Tasks 28–35)

### Task 28: Frontend Project Initialization

- Create: `frontend/package.json`, `frontend/vite.config.js`, `frontend/public/index.html`, `frontend/src/main.js`, `frontend/env.example`
- Implements: Vue 3 + Vite + Naive UI setup

- [ ] **Step 28.1–28.6:** (Vite scaffold, dependencies, entry point per plan)

- [ ] **Step 28.7: Commit**

```bash
git add frontend/package.json frontend/vite.config.js frontend/public/index.html frontend/src/main.js frontend/env.example
git commit -m "feat: bootstrap Vue 3 + Vite + Naive UI frontend"
```

---

### Task 29: Router & Layout Component

- Create: `frontend/src/router/index.js`, `frontend/src/App.vue`, `frontend/src/components/Layout.vue`
- Implements: Vue Router with routes (stations, models, tasks, reports), main layout with nav

- [ ] **Step 29.1–29.5:** (Router config, layout structure per plan)

- [ ] **Step 29.6: Commit**

```bash
git add frontend/src/router/ frontend/src/App.vue frontend/src/components/Layout.vue
git commit -m "feat: implement Vue Router and main layout"
```

---

### Task 30: Stations & Models Management Pages

- Create: `frontend/src/components/StationForm.vue`, `frontend/src/components/StationList.vue`, `frontend/src/components/ModelList.vue`, `frontend/src/components/ModelFetch.vue`, `frontend/src/views/StationsView.vue`
- Implements: Station CRUD UI, model list display, model fetch trigger

- [ ] **Step 30.1–30.8:** (Form components, API integration, error handling per plan)

- [ ] **Step 30.9: Commit**

```bash
git add frontend/src/components/StationForm.vue frontend/src/components/ModelList.vue frontend/src/views/StationsView.vue
git commit -m "feat: implement station and model management UI"
```

---

### Task 31: Detection Task Form & Progress Viewer

- Create: `frontend/src/components/TaskForm.vue`, `frontend/src/components/TaskProgress.vue`, `frontend/src/views/TasksView.vue`
- Implements: Create detection task, real-time SSE progress with probe status grid

- [ ] **Step 31.1–31.8:** (Task form with config options, SSE EventSource, progress grid per plan)

- [ ] **Step 31.9: Commit**

```bash
git add frontend/src/components/TaskForm.vue frontend/src/components/TaskProgress.vue frontend/src/views/TasksView.vue
git commit -m "feat: implement detection task creation and SSE progress tracking"
```

---

### Task 32: Chart Components (ECharts Integration)

- Create: `frontend/src/components/AuthenticityChart.vue`, `frontend/src/components/PerformanceChart.vue`, `frontend/src/components/BillingChart.vue`, `frontend/src/components/CapabilityMatrix.vue`, `frontend/src/utils/chart_config.js`
- Implements: Radar (overall + authenticity), bar (signals), line (TTFT/throughput), table (capability)

- [ ] **Step 32.1–32.8:** (ECharts setup, color scheme, data binding per plan)

- [ ] **Step 32.9: Commit**

```bash
git add frontend/src/components/*Chart.vue frontend/src/utils/chart_config.js
git commit -m "feat: implement ECharts visualizations for reports"
```

---

### Task 33: Report Display & PDF Export

- Create: `frontend/src/components/ReportView.vue`, `frontend/src/views/ReportView.vue`, `frontend/src/utils/sse_client.js`
- Implements: Comprehensive report with 9 blocks, chart embedding, PDF export trigger

- [ ] **Step 33.1–33.8:** (Report assembly from API, chart screenshot capture via `getDataURL()`, PDF POST per plan)

- [ ] **Step 33.9: Commit**

```bash
git add frontend/src/components/ReportView.vue frontend/src/views/ReportView.vue frontend/src/utils/sse_client.js
git commit -m "feat: implement report visualization and PDF export"
```

---

### Task 34: API Client & State Management

- Create: `frontend/src/api/client.js`, `frontend/src/api/stations.js`, `frontend/src/api/models.js`, `frontend/src/api/tasks.js`, `frontend/src/api/reports.js`, `frontend/src/stores/main.js`
- Implements: Axios client, per-resource API methods, Pinia store

- [ ] **Step 34.1–34.8:** (HTTP interceptors, Pinia composables, state sync per plan)

- [ ] **Step 34.9: Commit**

```bash
git add frontend/src/api/ frontend/src/stores/
git commit -m "feat: implement API client and Pinia state management"
```

---

### Task 35: Styling & Utility Functions

- Create: `frontend/src/utils/format.js`, `frontend/src/assets/styles.css`
- Implements: Format score, date, numbers; localize authenticity levels; responsive CSS

- [ ] **Step 35.1–35.5:** (Number/date formatting, Chinese translations, CSS per plan)

- [ ] **Step 35.6: Commit**

```bash
git add frontend/src/utils/format.js frontend/src/assets/
git commit -m "feat: add utility formatters and styling"
```

---

## Phase 7: Documentation & Final Integration (Tasks 36–38)

### Task 36: README & Setup Guide

- Create: `README.md`
- Documents: Project overview, setup steps, usage guide, license (MIT © 2026)

- [ ] **Step 36.1–36.5:** (Installation, running backend+frontend, config, architecture diagram per plan)

- [ ] **Step 36.6: Commit**

```bash
git add README.md
git commit -m "docs: add project README with setup guide"
```

---

### Task 37: Update TODO.md & Project Closure

- Update: `doc/TODO.md` with all Phase 2–5 tasks marked as complete
- Create: Release notes summarizing delivery

- [ ] **Step 37.1–37.3:** (Mark tasks done, document final state per plan)

- [ ] **Step 37.4: Commit**

```bash
git add doc/TODO.md
git commit -m "docs: mark all implementation tasks complete"
```

---

### Task 38: Local Docker Compose (Optional, After Time Zone Confirmation)

- Create: `Dockerfile`, `docker-compose.yml`
- Implements: Backend + frontend services, volumes, networks

- [ ] **Step 38.1–38.5:** (Containerization, service orchestration per plan; **deferred until user confirms time zone requirement**)

- [ ] **Step 38.6: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "devops: add Docker containerization (optional)"
```

---

## Execution Guidance

**Total Tasks**: 38 (broken into 127 steps)

**Time Estimate**: ~2–3 weeks at 5–6 hrs/day development

**Recommended Parallelization**:
- Tasks 1–5 (backend infra) must complete sequentially
- Tasks 6–8 (adapters) can run in parallel after Task 5
- Tasks 9–14 (probes) can run in parallel after Task 8
- Tasks 15–20 (engine/scoring) must await Task 14, then can run in parallel
- Tasks 21–27 (API) depend on Task 20, can run in parallel
- Tasks 28–35 (frontend) can start after Task 21 (API layer defined)
- Tasks 36–38 (docs/devops) run in parallel with frontend or at end

**Quality Gates**:
- All unit tests must pass before integration tests
- All integration tests must pass before E2E tests
- Zero-network constraint: every test uses fixtures, no real API keys
- Deidentification: all error paths must pass security unit tests before merge

