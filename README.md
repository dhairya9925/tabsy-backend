# Splitwise Buddy Backend API — Mobile & Client Developer Guide

FastAPI REST API powering Splitwise Buddy across web and native mobile applications (iOS and Android).

All endpoints follow strict REST conventions, use 100% stateless HTTP Bearer token authentication, return the standardized `{ data, error, meta }` response envelope, and are documented via OpenAPI 3.x.

---

## Table of Contents

1. [Base URLs & Endpoints](#1-base-urls--endpoints)
2. [OpenAPI Specification & Client SDK Generation](#2-openapi-specification--client-sdk-generation)
3. [Mobile Authentication & Token Lifecycle](#3-mobile-authentication--token-lifecycle)
4. [Standard Response Envelope](#4-standard-response-envelope)
5. [Error Handling & Status Codes](#5-error-handling--status-codes)
6. [Rate Limiting & Header Standards](#6-rate-limiting--header-standards)
7. [Distributed Tracing & Correlation IDs](#7-distributed-tracing--correlation-ids)
8. [API Versioning & Deprecation Policy](#8-api-versioning--deprecation-policy)
9. [Running the Backend Locally](#9-running-the-backend-locally)

---

## 1. Base URLs & Endpoints

### Environments

| Environment | Base URL | Notes |
| :--- | :--- | :--- |
| **Local (iOS Simulator / macOS)** | `http://localhost:8000` | Local development host |
| **Local (Android Emulator)** | `http://10.0.2.2:8000` | Android emulator loopback alias |
| **Local (Physical Device / LAN)** | `http://<YOUR_LOCAL_IP>:8000` | Device on same Wi-Fi network |
| **Staging** | `https://staging-api.splitwisebuddy.com` | Staging cluster |
| **Production** | `https://api.splitwisebuddy.com` | Production cluster |

All application endpoints are routed under the `/api/v1` namespace (e.g. `GET /api/v1/users/me`).

---

## 2. OpenAPI Specification & Client SDK Generation

FastAPI exposes the full OpenAPI 3.x schema covering all endpoints, request bodies, query filters, and response models.

### Endpoints
- **OpenAPI JSON**: `GET /openapi.json` and `GET /api/v1/openapi.json`
- **Interactive Swagger UI**: `GET /docs`
- **ReDoc Documentation**: `GET /redoc`

### Generating Native Mobile SDKs

Use OpenAPI Generator to generate type-safe client models and API classes:

#### Swift (iOS)
```bash
# Using OpenAPI Generator CLI
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g swift5 \
  -o ./ios/SplitwiseAPIKit \
  --additional-properties=responseAs=AsyncAwait,projectName=SplitwiseAPIKit
```

#### Kotlin (Android)
```bash
# Using OpenAPI Generator CLI
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g kotlin \
  -o ./android/splitwise-api \
  --additional-properties=library=jvm-ktor,serializationLibrary=kotlinx_serialization
```

#### Flutter / Dart
```bash
npx @openapitools/openapi-generator-cli generate \
  -i http://localhost:8000/openapi.json \
  -g dart-dio \
  -o ./mobile/packages/splitwise_api
```

---

## 3. Mobile Authentication & Token Lifecycle

The backend is **100% stateless** and relies exclusively on Supabase JWT verification. The server does not use, issue, or read HTTP session cookies.

### 3.1 Obtaining Tokens (Supabase Auth REST API)
Mobile clients obtain and refresh tokens by calling Supabase Auth REST endpoints directly (or via the official Supabase Swift / Kotlin SDK):

#### Email / Password Sign In
```http
POST {SUPABASE_URL}/auth/v1/token?grant_type=password
Content-Type: application/json
apikey: {SUPABASE_ANON_KEY}

{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```
**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "u_k8V9...e9Q",
  "user": { ... }
}
```

#### Token Refresh
Access tokens expire after 1 hour (3600 seconds). Mobile apps should proactively refresh tokens before expiry or automatically retry upon receiving an HTTP `401 Unauthorized`:
```http
POST {SUPABASE_URL}/auth/v1/token?grant_type=refresh_token
Content-Type: application/json
apikey: {SUPABASE_ANON_KEY}

{
  "refresh_token": "u_k8V9...e9Q"
}
```

### 3.2 Authenticating API Requests
Send the `access_token` in the standard HTTP `Authorization` header on all protected requests:

```http
GET /api/v1/dashboard/summary HTTP/1.1
Host: localhost:8000
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Accept: application/json
```

> **Security Note:** Never send user IDs in request bodies or query parameters to assert caller identity. The backend derives authenticated user identity strictly from the verified JWT `sub` claim.

---

## 4. Standard Response Envelope

Every endpoint returns the standard three-key envelope:

```json
{
  "data": ...,
  "error": null,
  "meta": { ... }
}
```

### Successful Read Example (`GET /api/v1/users/me`):
```json
{
  "data": {
    "id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
    "user_id": "aaaaaaaa-9999-4999-a999-999999999999",
    "display_name": "Jane Doe",
    "email": "jane@example.com",
    "avatar_url": null,
    "is_shadow": false,
    "created_at": "2026-09-10T00:00:00Z",
    "updated_at": "2026-09-10T00:00:00Z"
  },
  "error": null,
  "meta": null
}
```

### Successful Write Example (`POST /api/v1/expenses/personal` -> `201 Created`):
```json
{
  "data": {
    "id": "e3b0c442-98fc-1c14-9afb-4c72e04f9e1e",
    "amount": 42.50,
    "category": "groceries",
    "note": "Weekly Grocery run",
    "expense_date": "2026-09-10",
    "user_id": "aaaaaaaa-9999-4999-a999-999999999999",
    "created_at": "2026-09-10T02:00:00Z"
  },
  "error": null,
  "meta": null
}
```

---

## 5. Error Handling & Status Codes

When an error occurs, the HTTP status code reflects the category, `data` is `null`, and `error` contains a clear message:

```json
{
  "data": null,
  "error": "Expense with ID 9b1deb4d-... was not found",
  "meta": null
}
```

### Validation Errors (`422 Unprocessable Entity`)
```json
{
  "data": null,
  "error": "Validation error: body.amount: Input should be greater than 0",
  "meta": {
    "details": [
      {
        "type": "greater_than",
        "loc": ["body", "amount"],
        "msg": "Input should be greater than 0",
        "input": -5.0
      }
    ]
  }
}
```

### Standard Status Codes

| Status Code | Reason | Mobile Recommended Action |
| :--- | :--- | :--- |
| **`200 OK`** | Request succeeded | Process `data`. |
| **`201 Created`** | Resource created | Update local cache / UI with new item. |
| **`400 Bad Request`** | Malformed request or domain invariant violation | Display `error` to user. |
| **`401 Unauthorized`** | Missing, expired, or invalid token | Trigger token refresh; redirect to Login if refresh fails. |
| **`403 Forbidden`** | Insufficient permissions (e.g. non-member accessing group) | Show permission error; do not retry. |
| **`404 Not Found`** | Resource does not exist or has been deleted | Remove item from local state. |
| **`409 Conflict`** | State conflict (e.g. duplicate friend request, already finalized) | Refresh resource state from server. |
| **`422 Unprocessable Entity`** | Schema validation error | Inspect `meta.details` to highlight form fields. |
| **`429 Too Many Requests`** | Rate limit exceeded | Pause requests for `Retry-After` seconds. |
| **`500 Internal Server Error`** | Unexpected server failure | Log error with `X-Request-ID`; prompt user to retry later. |

---

## 6. Rate Limiting & Header Standards

The API implements a sliding window rate limiter per client key (authenticated token or IP):

- **Default Limit**: 120 requests per 60-second window.
- **Exempt Endpoints**: Health checks (`/api/v1/health`) and OpenAPI docs (`/openapi.json`, `/docs`).

### Rate Limit Headers

Every HTTP response includes standard rate limit headers:
- `X-RateLimit-Limit`: Maximum requests permitted per window (e.g. `120`).
- `X-RateLimit-Remaining`: Number of requests remaining in current window.

### Rate Limit Exceeded (`429 Too Many Requests`)
When throttled, the server returns:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 42
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 42
X-Request-ID: b943a53e-1b8e-4a69-9524-7b925bdfa91e
Content-Type: application/json

{
  "data": null,
  "error": "Rate limit exceeded. Please retry in 42 seconds.",
  "meta": {
    "retry_after": 42
  }
}
```
**Client Best Practice**: Inspect the `Retry-After` header and back off before retrying.

---

## 7. Distributed Tracing & Correlation IDs

Mobile clients should supply a unique UUID in the `X-Request-ID` header on each HTTP request:

```http
GET /api/v1/expenses/personal HTTP/1.1
Host: localhost:8000
Authorization: Bearer <token>
X-Request-ID: 9a2f7c01-72f8-4824-8149-a2123fa75210
```

- If provided, the server preserves and echoes back `X-Request-ID`.
- If omitted, the server automatically generates a correlation UUID.
- All server-side structured JSON logs record this `request_id`, enabling end-to-end tracing across mobile telemetry (Sentry, Crashlytics) and backend logs.

---

## 8. API Versioning & Deprecation Policy

### Path-Based Versioning
All endpoints are strictly path-versioned under `/api/v1/`.

### Non-Breaking Schema Changes
The backend follows backward-compatible evolution guidelines:
- New fields added to response models (`{ data: ... }`) are non-breaking. Mobile JSON decoders should ignore unknown keys (e.g. in Swift: standard `Codable`; in Kotlin: `ignoreUnknownKeys = true` in `Json {}`).
- New optional query parameters or request body fields are non-breaking.
- Existing endpoint URLs and field names are immutable within `v1`.

### Breaking Changes & Deprecation Policy
If a breaking schema alteration or behavioral overhaul is unavoidable:
1. A new versioned prefix (`/api/v2/`) will be introduced in parallel.
2. The `/api/v1/` endpoints will remain fully operational and supported for a minimum of **6 months** after `v2` release to allow mobile client store rollout.
3. Advance notices will include migration guides and deprecation headers (`Sunset` / `Deprecation`).

---

## 9. Running the Backend Locally

```bash
# Navigate to backend directory
cd backend

# Install dependencies via uv
uv sync

# Run database migrations (if required)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Running Test Suites
```bash
# Run all backend unit & integration tests
uv run pytest tests/

# Run mobile readiness tests specifically
uv run pytest tests/test_mobile_readiness.py -v
```
