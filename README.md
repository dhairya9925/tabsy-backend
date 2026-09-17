# Splitwise Buddy Backend API

FastAPI backend providing REST API endpoints for Splitwise Buddy.
All endpoints require JWT authentication (`Bearer <token>`) and follow the standard ResponseEnvelope `{ data, error, meta }`.

## Key Feature Modules
- **Authentication**: Self-hosted bcrypt + HS256 JWT auth (`/api/v1/auth/*`).
- **Friends & Contacts**: 1-on-1 expense sharing, shadow contacts, friend requests, and balance settling.
- **Groups**: Support for 5 group archetypes (`shared_living`, `trip`, `day_to_day`, `event`, `reimbursable`).
- **Shared Living Monthly Household Ledger**:
  - `GET /api/v1/groups/{id}/monthly-ledger?month={1-12}&year={>=2020}`: Full self-balancing ledger replicating household spreadsheet accounting. Calculates per-member obligations with whole-rupee ceiling rounding (`ceil`), balances, personal `my_summary` (with 1-tap UPI deep links), and coordinator clearing checklists.
  - `POST /api/v1/groups/{id}/monthly-ledger/contributions?month={1-12}&year={>=2020}`: Record and confirm member monthly payments.
  - `POST /api/v1/groups/{id}/monthly-ledger/disbursements?month={1-12}&year={>=2020}`: Record coordinator outflows for vendor bills (e.g. Landlord Rent from pool funds with zero personal double-counting) and member refunds.
  - `POST /api/v1/groups/{id}/monthly-ledger/lock?month={1-12}&year={>=2020}`: Lock the monthly cycle and optionally carry forward unrefunded overpayments to next month's ledger.
- **Dashboard**: Aggregated financial metrics and real-time activity feeds (`/api/v1/dashboard/summary`).

## Testing
```bash
uv run pytest
```

