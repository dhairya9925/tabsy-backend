# Tabsy Backend

FastAPI-powered REST API backend for **Tabsy** (Expense Manager) with self-hosted JWT authentication, PostgreSQL with async SQLAlchemy, and Docker deployment support.

## Repositories

- **Backend**: https://github.com/dhairya9925/tabsy-backend (This repo)
- **Frontend**: https://github.com/dhairya9925/tabsy-frontend
- **Mobile**: https://github.com/dhairya9925/tabsy-mobile

## Features

- **Self-Hosted Auth**: Secure HS256 JWT auth with bcrypt password hashing (`/api/v1/auth/*`).
- **Standardized Response Envelope**: All endpoints return `{ data, error, meta }`.
- **Expense Management**: Personal, group, and friend expense logging, splitting, and settlement calculations.
- **Group Living & Ledgers**: Monthly household ledger, rollover credits, and disbursements.
- **Async PostgreSQL**: Built on SQLAlchemy 2.0 async engine and `asyncpg`.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended package manager)
- PostgreSQL (or Supabase instance)

### Local Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/dhairya9925/tabsy-backend.git
   cd tabsy-backend
   ```

2. Set up environment variables:
   ```bash
   cp .env.example .env
   # Update DATABASE_URL and JWT_SECRET in .env
   ```

3. Create virtual environment and install dependencies:
   ```bash
   uv venv
   source .venv/bin/activate
   uv pip install -r requirements.txt
   ```

4. Run the development server:
   ```bash
   uv run uvicorn app.main:app --reload --port 8000
   ```

5. Explore the interactive API documentation at `http://localhost:8000/docs`.

### Running with Docker

```bash
docker compose up --build
```

### Running Tests

```bash
uv run pytest
```
