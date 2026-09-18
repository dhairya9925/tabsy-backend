# Tabsy Backend

FastAPI-powered REST API backend for **Tabsy** (Expense Manager) with self-hosted JWT authentication, PostgreSQL with async SQLAlchemy, and Docker deployment support.
All endpoints require JWT authentication (`Bearer <token>`) and follow the standard ResponseEnvelope `{ data, error, meta }`.

## Repositories

- **Backend**: https://github.com/dhairya9925/tabsy-backend (This repo)
- **Frontend**: https://github.com/dhairya9925/tabsy-frontend
- **Mobile**: https://github.com/dhairya9925/tabsy-mobile

## Features

- **Self-Hosted Auth**: Secure HS256 JWT auth with bcrypt password hashing (`/api/v1/auth/*`).
- **Standardized Response Envelope**: All endpoints return `{ data, error, meta }`.
- **Expense Management**: Personal, group, and friend expense logging, splitting, and settlement calculations.
- **Friends & Contacts**: 1-on-1 expense sharing, shadow contacts, friend requests, and balance settling.
- **Groups**: Support for 5 group archetypes (`shared_living`, `trip`, `day_to_day`, `event`, `reimbursable`).
- **Shared Living Monthly Household Ledger**:
  - `GET /api/v1/groups/{id}/monthly-ledger?month={1-12}&year={>=2020}`: Self-balancing ledger replicating household spreadsheet accounting. Calculates per-member obligations with whole-rupee ceiling rounding (`ceil`), balances, personal summary (with 1-tap UPI deep links), and coordinator clearing checklists.
  - `POST /api/v1/groups/{id}/monthly-ledger/contributions`: Record and confirm member monthly payments.
  - `POST /api/v1/groups/{id}/monthly-ledger/disbursements`: Record coordinator outflows for vendor bills and member refunds.
  - `POST /api/v1/groups/{id}/monthly-ledger/lock`: Lock monthly cycle and optionally carry forward rollover credits.
- **Dashboard**: Aggregated financial metrics and real-time activity feeds (`/api/v1/dashboard/summary`).
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

5. Explore interactive API documentation at `http://localhost:8000/docs`.

### Running with Docker

```bash
docker compose up --build
```

### Running Tests

```bash
uv run pytest
```
