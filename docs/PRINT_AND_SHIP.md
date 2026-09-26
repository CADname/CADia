# Print & Ship — CAD to Physical Production

CADia's **Print & Ship** workflow connects the same editable B-Rep model used for AI CAD creation and follow-up editing to a real production-quoting and shipping flow.

The implementation is deliberately an extension of CADia's existing manufacturing subsystem. It does not introduce a second geometry core or a separate mesh-generation path.

## End-to-end flow

```text
Natural-language request / direct CAD editing
        ↓
Editable OCCT / CadQuery B-Rep
        ↓
Current CADia project runtime
        ↓
STL export from the active model
        ↓
3D-print DFM / 220 × 220 × 220 mm production preflight
        ↓
Slant 3D MCP provider adapter
        ↓
Live material + color inventory
        ↓
Live production quote
        ↓
Quantity pricing
        ↓
Destination-based shipping options
        ↓
Production / checkout path
```

The public hackathon deployment intentionally stops before payment and final order creation. Quote and shipping calculation are live; accidental purchases are blocked server-side with `CADIA_SLANT3D_DEMO_MODE=true`.

## Implementation

### 1. Provider adapter

`web/backend/standalonecad_web/manufacturing/slant3d.py`

The adapter implements a small MCP Streamable HTTP client using `httpx` and connects to the configured Slant 3D MCP endpoint.

Rather than hard-coding one request payload shape, the adapter:

1. initializes an MCP session,
2. calls `tools/list`,
3. reads the selected tool's `inputSchema`,
4. maps CADia's canonical fields to the provider schema,
5. calls the provider tool,
6. unwraps structured or text MCP responses.

Aliases are used for fields such as STL content, quote ID, material/filament ID, quantity, address fields, and shipping service. This keeps the provider-specific schema isolated from the CAD core.

### 2. Manufacturing API

`web/backend/standalonecad_web/routes/manufacturing.py`

The existing project-scoped manufacturing router now exposes the fulfillment flow:

```text
GET  /api/projects/{project_id}/manufacturing/fulfillment/slant3d/materials
POST /api/projects/{project_id}/manufacturing/fulfillment/slant3d/quote
POST /api/projects/{project_id}/manufacturing/fulfillment/slant3d/quantity
POST /api/projects/{project_id}/manufacturing/fulfillment/slant3d/shipping
POST /api/projects/{project_id}/manufacturing/fulfillment/slant3d/checkout
```

The quote route:

- resolves the current project CAD runtime,
- generates the current mesh/STL from the active CAD model,
- runs CADia's DFM checks against a 220 × 220 × 220 mm production envelope,
- rejects failed preflight before provider submission,
- enforces a configurable STL upload-size limit,
- base64-encodes the STL,
- submits the manufacturing-ready file to the provider,
- returns the live quote together with CADia's preflight result.

The shipping route validates recipient, email, region, postal code, and ISO country code before making an external request. Provider/MCP diagnostics are kept in server logs, while the browser receives short user-facing errors.

### 3. Provider capability discovery

`web/backend/standalonecad_web/manufacturing/capabilities.py`

Slant 3D fulfillment is exposed through CADia's existing manufacturing capability endpoint. This lets the UI discover whether fulfillment is enabled, whether demo mode is active, and which production envelope is being used.

### 4. Browser UX

`web/frontend/src/components/ManufacturingPanel.tsx`

The Manufacturing panel keeps the existing STEP / 3MF / DFM / G-code / printer-delivery tools and adds a separate **Print & Ship** workflow.

The user sees normal manufacturing controls rather than MCP data:

- production material selector,
- provider-available color selector,
- quantity selector,
- live production quote,
- shipping-address form,
- calculated shipping options.

Material inventory loads automatically in the background. Provider UUIDs, raw MCP payloads, STL hashes, and schema errors are not shown in the end-user UI.

### 5. Error handling

`web/frontend/src/api.ts` and the backend manufacturing routes convert nested FastAPI/provider errors into readable UI messages. Raw protocol errors remain available in server logs for debugging.

### 6. Deployment configuration

`.env.example` and `docker-compose.yml` expose the fulfillment settings:

```env
CADIA_SLANT3D_ENABLED=true
CADIA_SLANT3D_DEMO_MODE=true
CADIA_SLANT3D_MCP_URL=https://www.slant3d.com/mcp
CADIA_SLANT3D_TIMEOUT_SECONDS=45
CADIA_SLANT3D_MAX_STL_BYTES=20971520
```

`CADIA_SLANT3D_DEMO_MODE=true` is the safe public-demo default. In this mode, the checkout endpoint returns HTTP 403 and does not create a payment or order.

## Why this belongs inside CADia

The objective is not only to generate a 3D object. CADia keeps one engineering model alive from intent through repeated editing and downstream manufacturing.

> **Describe it → Design it → Refine it → Verify it → Manufacture it**

This makes the browser workflow a bridge between natural-language intent, editable engineering geometry, and a real physical-production path.
