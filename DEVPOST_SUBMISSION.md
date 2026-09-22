# InfinityX Global Hackathon 2K26 — Devpost Submission Copy

## Project name

CADia

## Tagline

AI-native editable CAD: create, import, select, and continuously modify real B-Rep models with natural language.

## Live demo

https://app.cadia.co.kr

Quick access: click **Launch CADia**. No CADia registration is required to inspect the guest workspace. Live AI generation requires connecting a supported AI provider.

## Source code

https://github.com/CADname/CADia

## Demo video

https://youtu.be/L2ocXoW0v_4

## Inspiration / problem statement

Modern CAD is powerful, but using it still requires learning many commands, feature operations, constraints, topology references and repetitive editing steps. Generative AI can make 3D content easier to create, but a visually plausible mesh is not the same thing as a reusable CAD model: it may not retain editable dimensions, feature history, persistent topology or a reliable downstream workflow.

CADia asks a different question: what if anyone could describe a design, open an existing model, point directly at the geometry they mean, and keep modifying the same real CAD model through natural language?

## What it does

CADia is an AI-native CAD system for creating and editing real B-Rep models. Native parametric models retain feature/history-aware state, while imported STEP/BREP geometry can be edited through direct CAD operations. Users can begin with a natural-language request or continue from existing geometry, then refine the same model through follow-up instructions and direct face/edge/object selection.

The AI interprets intent and chooses typed CAD operations; the OCCT/CadQuery modeling kernel performs the geometry work and owns the model state. When a requested edit maps unambiguously to feature history, CADia can update the driving feature or parameter and rebuild downstream geometry. When a history-based path is not appropriate, applicable direct B-Rep editing operations provide another route. Verification, rollback and recovery protect the active model across repeated changes.

## Who it helps / real-world value

CADia targets the gap between “I can describe what I want” and “I can keep engineering the result.” Students and first-time CAD users can work through language and direct selection without memorizing every command sequence, while makers, designers and experienced CAD users can use the same workflow for repetitive creation and modification. Because the result remains B-Rep CAD and can be exported through STEP, the workflow can continue beyond the AI interaction instead of ending at a visual mesh.

## Why it is different

CADia does not treat an AI-generated mesh as the final product. Its source of truth is a B-Rep CAD model with editable CAD state.

The main technical separation is deliberate:

1. The language model interprets design intent.
2. A structured, typed CAD tool layer constrains what can be executed.
3. The OCCT/CadQuery kernel constructs and modifies the model.
4. Verification, transaction rollback and bounded recovery protect model state.
5. Feature history and topology-aware selection keep the result editable.

CADia also implements persistent topology descriptors and history-aware editing. A face or edge selected in the browser is connected back to CAD topology rather than treated as an anonymous triangle selection. After model changes, CADia uses topology class, geometric properties and model/history context to rebind references and rejects ambiguous matches. When an edit can be traced unambiguously to a driving feature or parameter, the system changes that source and rebuilds downstream geometry. Imported geometry and operations without a usable history path can use direct B-Rep editing.

The editing flow is therefore: **natural language and/or selection -> topology resolution -> history-aware parameter/feature edit when unambiguous -> downstream rebuild -> topology rebinding -> verification**, with a direct B-Rep edit path for geometry without a usable parametric-history path. This makes editing, not just first-pass generation, a core part of the system.

## Key features

- Natural language to real B-Rep CAD
- Continuous natural-language modification of an existing model
- Feature history and driving parameters
- Persistent face/edge topology and topology rebinding
- Face, edge and object selection in the browser
- History-aware and direct B-Rep editing paths
- Sketch constraints and parametric expressions
- Assemblies, constraints, BOM queries and kinematic joints
- Parametric and standard-component generators, including gear-oriented workflows
- Verification, atomic rollback and automatic recovery
- STEP, STL and 3MF export paths
- 3D-print DFM checks and PrusaSlicer-based slicing/G-code workflows
- 118 project-scoped CAD tools exposed to the AI integration layer
- ChatGPT/Codex, GitHub Copilot, OpenAI API, Claude and Gemini integration paths sharing one CAD core
- Isolated guest workspace for hackathon evaluation

## How we built it

The CAD engine is written in Python around Open CASCADE through OCP/CadQuery. It owns the parametric document, feature history, topology records, parameter graph, sketch/feature operations, assembly relationships and verification/recovery behavior.

The web backend uses FastAPI, SQLAlchemy and PostgreSQL. Each project gets a CAD runtime, while AI access is routed through a project-scoped MCP-compatible gateway. The web tool surface contains 118 operations: a 58-tool compatibility contract, 10 assembly-joint extensions and 50 native CADia extensions.

The frontend is React, TypeScript and Three.js. Browser geometry is generated from the CAD runtime for visualization and interaction while persistent topology identifiers remain connected to the B-Rep source of truth.

For deployment, CADia uses Docker Compose, Nginx and AWS Lightsail. The judging build adds an isolated one-click guest workflow so judges can enter the CAD workspace without registering a CADia account.

## Technical architecture

```text
Natural-language request + current CAD state + selection
  -> intent planning against a typed CAD tool catalog
  -> plan/schema validation
  -> deterministic CAD operations
  -> OCCT / CadQuery B-Rep kernel
  -> verification + rollback + recovery
  -> editable feature history + persistent topology
  -> browser interaction / assembly / manufacturing export
```

## Scalability and feasibility

CADia separates the AI-provider layer from the CAD core: ChatGPT/Codex, GitHub Copilot and API-based providers all feed the same project-scoped CAD execution surface rather than requiring separate modeling engines. Projects and provider credentials are isolated per user, and the web deployment is containerized with FastAPI, PostgreSQL, Nginx and Docker Compose. This makes additional AI providers, CAD tools and downstream workflows extensible without replacing the geometry kernel.

## Challenges

The hardest problem was not producing a shape once. It was preserving design meaning across edits. CAD topology can change after a Boolean, fillet or feature rebuild, so a face that existed before an operation cannot safely be identified only by its old index. CADia therefore tracks persistent descriptors and uses topology class, geometric properties and history context to rebind references, while rejecting ambiguous matches.

Another challenge was separating probabilistic language understanding from deterministic geometry execution. CADia keeps the language model on the intent/planning side and pushes actual modeling into typed operations and the CAD kernel. Plans are validated before execution, and verification/recovery validates the resulting CAD operations and model state.

Finally, the web version had to preserve an interactive CAD workflow while isolating users, projects and AI-provider credentials on a deployable server.

## Accomplishments we are proud of

- Built an AI interface around a real B-Rep/parametric CAD state rather than a disposable mesh pipeline.
- Preserved feature history and topology-aware editing across natural-language follow-up requests.
- Connected interactive browser face/edge selection back to persistent CAD topology identifiers.
- Unified multiple AI providers behind one CAD execution surface instead of maintaining provider-specific modeling engines.
- Extended the workflow from single parts into assemblies, joints and manufacturing-oriented export.
- Added transaction, verification and recovery behavior that preserves the last valid model state across repeated edits.
- Delivered a judge-accessible web application with isolated guest workspaces.

## What we learned

AI is strong at interpreting design intent, but a deterministic CAD kernel should remain responsible for geometry and model state. The most useful architecture was therefore not “LLM as CAD engine,” but “LLM as intent planner connected to deterministic CAD operations.”

We also learned that editability is a harder and more valuable problem than one-shot generation. A model becomes substantially more useful when its dimensions, history and topology remain connected after the first result appears.

## What's next

Next steps include broader validation across diverse CAD creation and editing tasks, improved recovery coverage, richer collaborative workflows, and deeper downstream verification.

## Built with

Python, Open CASCADE, OCP, CadQuery, FastAPI, PostgreSQL, SQLAlchemy, React, TypeScript, Three.js, Vite, Docker, Nginx, AWS Lightsail, MCP, Codex App Server, GitHub Copilot SDK, OpenAI API, Anthropic API, Gemini API.
