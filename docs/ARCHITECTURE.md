# CADia Architecture

## System overview

```text
Browser: React + Three.js
        |
      HTTPS
        v
Nginx -> FastAPI web application
              |
              +-> PostgreSQL project / auth state
              +-> project-scoped CAD runtime
              +-> AI-provider adapter layer
              +-> MCP-compatible CAD gateway
              +-> manufacturing/export services
                        |
                        v
                CADia CAD core
                        |
            OCCT / OCP / CadQuery B-Rep
                        |
              history / topology / verifier
```

The browser mesh is a visualization layer. CADia keeps the B-Rep CAD model as the source of truth.

## Request-to-geometry path

1. The request is interpreted together with the current CAD state and direct selection context.
2. The AI layer chooses structured CAD operations from the project-scoped tool surface.
3. Operation arguments are validated before geometry execution.
4. The CAD core executes deterministic operations against OCCT/CadQuery.
5. History-aware edits rebuild dependent geometry when a driving feature/parameter is available.
6. Direct B-Rep operations cover applicable edits where a history path is not appropriate.
7. Topology descriptors are rebound after model changes so browser selections can remain connected to CAD entities.
8. Verification, rollback and bounded recovery protect the last valid CAD state.

## Main code areas

| Path | Role |
| --- | --- |
| `src/standalonecad/core/` | B-Rep modeling, history, topology, assemblies, joints, generators, verifier |
| `src/standalonecad/planning.py` | deterministic CAD planning compatibility paths |
| `src/standalonecad/codex_agent.py` | AI planning integration |
| `web/backend/standalonecad_web/` | FastAPI web runtime, projects, providers, MCP gateway, manufacturing |
| `web/frontend/` | React / Three.js CAD interface |
| `tools/cadia_web_mcp_bridge.py` | CAD web/MCP bridge |
| `evidence/` | saved modeling outputs and prompts |

`standalonecad` and `standalonecad_web` are retained as internal Python package namespaces; the product and submission name is CADia.

## AI-provider separation

ChatGPT/Codex, GitHub Copilot, OpenAI API, Claude and Gemini connect through provider adapters while sharing the same project-scoped CAD execution surface. The geometry kernel and model state do not change with the selected AI provider.

## Manufacturing and export

The web application exposes STEP AP242, STL and 3MF export, 3D-print DFM checks, and PrusaSlicer-based 3D-print G-code generation.
