# TDG — Tactical Decision Game Platform

Web-based multiplayer tactical simulation with AI-controlled opponent forces,
collaborative map drawing, and structured order understanding.

## Quick Start

### 1. Prerequisites
- Python 3.12+
- Docker & Docker Compose

### 2. Start infrastructure
```bash
docker compose up -d
```
This launches PostgreSQL + PostGIS (port 5432) and Redis (port 6379).

> **Note:** If upgrading from a previous version that used `kshu` as the DB name,
> run `docker compose down -v` first to remove the old volume, then `docker compose up -d`.

### 3. Install Python dependencies
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### 4. Configure environment
```bash
copy .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 5. Seed the database (creates tables + sample scenario)
```bash
python -m scripts.seed_scenario
```

### 6. Start the backend
```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Open the frontend
Navigate to `http://localhost:8000` in your browser.

## Usage
1. Enter a display name and click **Register / Login**
2. Click **Create Session** to create a new game session
3. Click **Start Session** to initialize units from the scenario
4. Military unit symbols appear on the map
5. Use the **drawing toolbar** (top-left) to draw overlays (lines, polygons, markers)
6. Submit text orders in the **Orders** tab
7. View the game log in the **Log** tab

## API Documentation
FastAPI auto-generates interactive docs:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Project Structure
See `AGENTS.MD` for full architecture, domain model, and implementation roadmap.

## Tech Stack
| Layer | Technology |
|---|---|
| Frontend | Leaflet, Leaflet.Editable, milsymbol.js, Vanilla JS |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Cache/PubSub | Redis 7 |
| AI | OpenAI GPT-4.1 |
