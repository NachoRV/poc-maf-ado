# AGENTS.md

## Purpose
`poc-agentes-maf` is the **second iteration** of `/Users/irvb/Proyectos/poc-agentes` — same personal hands-on learning project, same user, new scope. The first PoC proved the *mechanism* (tool-calling loop, structured output, rules before LLM, deterministic rendering, full trace in `runs/`) over fake repos on disk. This one proves the *product*: the user describes the pipeline they need in a terminal chat, and the system opens a real Pull Request in Azure DevOps.

The master plan is [docs/Plan poc-ado.md](docs/Plan%20poc-ado.md) (Bloques 0-6, tasks T0.x-T5.x) — **read it before touching code**. It also records the decisions already made and closed (extends vs file copy, catalog layout in ADO, real PAT access); don't re-open them without a reason.

## The two shifts from `poc-agentes`
1. **Entry is a conversation, not a repo.** The old flow started with `fingerprint(ruta)` — deterministic signals read off files. This one starts with a chat. That moves the LLM from the *middle* of the flow (disambiguating an archetype) to the *front* (turning prose into structured data), where its output feeds everything downstream and there are no signals underneath it.
2. **Catalog and destinations live in Azure DevOps, not on disk.** And the final artifact is a branch, a commit and a PR — not a file in `runs/`.

## Critical rule: explain → implement → summarize
Inherited verbatim from `poc-agentes` (set 2026-09-04). The user wants to focus on understanding, not on typing code. For every task in the plan:
1. **Theory first.** Before touching any file, explain what's about to be built, why it exists, and who/what consumes it. Do not implement yet.
2. **Implement in small steps.** Once the user confirms they understand, write the code yourself — but **never create more than one file without first pausing to confirm the user understood its purpose**. One file at a time, small increments, check in after each.
3. **Summarize.** After implementing, recap what was built and the key concepts it demonstrates.

Exception, already applied: **T0.1 (scaffolding) was done in one pass** — empty directories, `.gitignore` and `requirements.txt` carry no concept to learn. The cadence above applies in full from T0.2 onwards.

## Continuity across conversations
Session memory resets between chats. To resume work here:
1. Read [docs/notas-agentes/Bitacora-de-sesiones.md](docs/notas-agentes/Bitacora-de-sesiones.md) first (newest entry on top) — what was done, the current state of the code, and the exact next step.
2. Read [docs/Plan poc-ado.md](docs/Plan%20poc-ado.md) for where that step sits in the plan.
3. The rules in this file apply to every future conversation on this repo.

## Documentation convention
Theory lives in Obsidian, not in the repo: `/Users/irvb/Library/Mobile Documents/iCloud~md~obsidian/Documents/Agentes IA/` (same vault as `poc-agentes` — this is a continuation, not a separate body of notes; link new notes to the existing ones). Every theoretical explanation given in chat, or an expansion of a previous one, must be written into the matching note in that folder without waiting to be asked. Tasks not yet covered in conversation stay as ⏳ placeholders — don't invent content ahead of what's actually been discussed.

The repo's `docs/notas-agentes/` keeps **only** the session log — operational continuity, not theory.

## Naming convention: `agente_*` means it calls a model (set 2026-09-17)
**A file whose name starts with `agente_` is a file that, when executed, ends up calling a language model. Any other file does not.** "When executed" is transitive, not literal: `requisitos/agente_recolector.py` doesn't import `llm/` — it imports `agente_extractor.py`, which does. What matters is whether running it costs model calls, not which line the call comes from.

The point is that "which parts of this system can hallucinate?" should be answerable from an `ls`, and stay answerable as the project grows. It is enforced, not remembered: `.venv/bin/python comprobar_agentes.py` walks the import graph and exits non-zero if a file calls the model without the prefix, or carries the prefix without calling it. Run it after adding any module.

Deliberate exception: the `llm/` package carries no prefix. Those aren't agents, they're the infrastructure agents use, and the folder name already says so.

Refinement added 2026-09-17: importing from `llm/` is **not** the same as calling the model. `llamadas_al_modelo` / `reiniciar_contador` are observability — `orquestacion/flujo.py` uses them to report how many calls happened, and one of its runs measures zero. Only `construir_cliente` / `kwargs_json` count. **Open point to settle when T3.2 lands:** once `flujo.py` imports `seleccion/agente_hibrido.py`, running it *will* cause model calls, and by the user's stated criterion it would need the prefix (`orquestacion/agente_flujo.py`) — even though it is the machine that runs agents, not an agent. Raise it then rather than decide it silently.

Files this will apply to as they get written: `seleccion/agente_hibrido.py`, `parametros/agente_generador.py`, and whatever writes the pull request descriptions. `parametros/variables.py` and everything under `render/` stay unprefixed — no model touches the artifact that gets deployed.

## Architecture
```
chat (LLM: slot filling) → requisitos.json → catálogo ADO → selección (reglas + LLM)
  → parámetros (derivación det. + LLM solo para huecos) → render (det.)
  → rama + commit + PR en ADO (det.) → runs/{timestamp}/
```
- `ado/` — **everything that speaks to Azure DevOps, isolated here.** It exposes domain functions (`listar_plantillas()`, `crear_pull_request(...)`); the rest of the system must not know ADO exists. If GitHub is ever needed, it's a sibling package with the same surface and nothing else changes. This boundary is the one architectural rule worth defending.
- `requisitos/` — the `Requisitos` Pydantic model and the conversational collector. **The LLM never decides when the conversation is done**: a deterministic `slots_que_faltan()` does. The model may only propose partial updates, which are validated and *merged* onto the previous state, never substituted for it.
- `seleccion/` — rules over `Requisitos` against the `applies_to` of the manifests discovered in ADO (never a hardcoded list). LLM only on `ambiguo`/`desconocido`, with a closed list and a 0.7 confidence threshold.
- `parametros/` — derives deterministically everything the requirements already fix (via `derived_from` in the manifest), and asks the LLM **only for the remaining gaps**.
- `render/` — deterministic YAML renderer, copied as-is from `poc-agentes`. The model never writes the artifact that gets deployed.
- `orquestacion/` — the state machine. `estados.py` declares, in code and not in a comment, whether each state is deterministic, LLM or hybrid; that table is what lets an execution report how many model calls it actually made.
- `runs/` — one folder per execution, including **the origin of every parameter value** (derived from a requirement vs generated by the model) and the PR URL.

## Environment & running code
- Python 3.14.6, virtualenv at `.venv/`. Activate with `source .venv/bin/activate`, or run scripts directly as `.venv/bin/python <file>`.
- Config in `.env` (gitignored); `.env.example` is versioned and documents every variable the project needs.
- **`AZDO_PAT` has write access to the user's repos.** Never print it, never let it reach a `runs/` folder (check that saved traces don't carry auth headers), never hardcode it. Minimum scopes: Code (Read & Write), Pull Request (Contribute).
- Model backend selected by `MODEL_BACKEND` (`gemini` | `lmstudio`), same pattern as `poc-agentes`. Gemini's free tier is **20 requests per day** — a single multi-turn slot-filling conversation can exhaust it, so `lmstudio` is the working default here, not just a convenience.

## Rules carried over from `poc-agentes` (learned the hard way)
- Don't trust state snapshots (`git status` in a prompt, session memory) without checking against the real repo. It has already caused wasted work twice.
- If an agent's instructions tell it to report a piece of data, that data must be literally in the tool's `return`. "It can be deduced from the result" is not enough — the model fills gaps with invented, plausible values and doesn't flag it. Re-check this on every new tool.
- When a new dependency forces a major-version bump of one already in use, re-verify the existing usage patterns against the new version before accepting it and updating the pin.
- Generated `runs/` folders are never committed; only the code that produces them.

## Reference
- [docs/Plan poc-ado.md](docs/Plan%20poc-ado.md) — full plan, all tasks and success criteria.
- `/Users/irvb/Proyectos/poc-agentes` — the previous iteration. Source of `render/renderizador.py`, the manifest/schema/template contract for the three templates, and the `guardar_run()` pattern.
