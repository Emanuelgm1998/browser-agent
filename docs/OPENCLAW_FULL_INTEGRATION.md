# OpenClaw Full Integration — Auditoría Fase 1

Fecha: 2026-09-11
Rama base: `benchmark-v0.2` (browser-agent)

---

## 1. Estado git previo (browser-agent)

```
Branch actual : benchmark-v0.2
Branches      : benchmark-v0.2 (*), main
Commit HEAD   : 2d62a65 Experiment A/B href: instrumentacion aditiva de contexto por step (JSONL) + include_attributes via overrides
│              d7dc201 Benchmark v0.2: campos aditivos para screening 4B/8B
│              1b9d838 Benchmark v0.2 fixes de atribucion y verificacion (evidencia 1.7b)
│              1ea7e99 Benchmark v0.2: capa de evaluacion independiente aislada del core
│              52f3394 Harness e2e estabilizado: watchdog absorbido + dedupe navigate (guarded_navigate) + REAL_TITLE via CDP ...
│              794d90f Harness e2e: prompt de estabilidad + max_steps=10 ...
│              330f64f Portar blindaje Qwen3 a browser-use 0.13.10 (qwen3_chat_ollama) + harness e2e_runner (ACL tools, think=False, repair JSON)
│              6e69fd2 Checkpoint: estado baseline browser-agent antes de portar blindaje Qwen3 (e2e)

Cambios sin commitear: benchmark/runner.py, benchmark/tasks.py, benchmark/verifiers.py
Archivos sin trackear   : launcher.cs, logo.ico, logo.svg
```

No hay `docs/` ni `README.md` en el proyecto actual.

## 2. Ubicaciones reales inspeccionadas

| Componente | Ruta verificada |
|---|---|
| Proyecto principal | `C:\Users\Cloud\browser-agent` |
| Entorno | `C:\Users\Cloud\browser-agent\.venv` (Python 3.14, run build por defecto sí) |
| WebUI Gradio existente | `C:\Users\Cloud\browser-use-webui` (webui.py, src/webui, src/browser, src/controller, src/agent) |
| OpenClaw CLI/paquete | `C:\Users\Cloud\AppData\Roaming\npm\node_modules\openclaw` (OpenClaw 2026.9.4, MIT) |
| Config OpenClaw | `C:\Users\Cloud\.openclaw\openclaw.json` |
| Estado OpenClaw | `C:\Users\Cloud\.openclaw\state\openclaw.sqlite` |
| Gateway OpenClaw | Tarea programada `OpenClaw Gateway`, bind `127.0.0.1:18789`, dashboard `http://127.0.0.1:18789/` |
| Companion (tray) | `C:\Users\Cloud\AppData\Local\OpenClawTray\OpenClaw.Tray.WinUI.exe` (activo) |
| Ollama | `http://127.0.0.1:11434` — modelos: qwen3:1.7b, qwen3:4b, qwen3:8b, qwen3-vl:4b |
| Perfil Chromium persistente | `C:\Users\Cloud\browser-agent\browser_profile` |

## 3. Arquitectura actual

### 3.1 Browser Agent (nuestro proyecto)

- **Núcleo estable (E2E)**: `e2e_runner.py` → `browser_use.Agent` + `Qwen3ChatOllama` + Playwright/Chromium.
  Blindaje ya probado:
  - `ACL_ACTIONS` (restringe acciones a navigate/click/input/search/extract/scroll/wait/go_back/find_elements/done)
  - `guarded_navigate` (deduplicación de navegación)
  - `REAL_TITLE` vía CDP (`Runtime.evaluate`) con verificación real de título
  - Watchdog absorbido + reintento con backoff (retry por firma de log)
  - JSON exclusivo por corrida (`write_json_exclusive`), trazas `.ctx.jsonl`
  - `qwen3_chat_ollama.py`: `think=False`, stripping de bloques de razonamiento, reparación de JSON defectuoso
- **Agente legado (V2)**: `agent_core.py` — bucle Playwright + Ollama (acción JSON simple). Sigue funcionando pero es anterior al blindaje.
- **Benchmark v0.2**: `benchmark/` con `tasks.py`, `runner.py`, `verifiers.py`, `fixtures_server.py`, fixtures HTML locales, reports.
  Línea base reportada: `example.com 15/15 (100%)`, `python.org 8/10 (80%)`, observed_title 10/10, 0 navegaciones fallidas.
- **UI**: `browser-use-webui` (Gradio, tema "Ocean" por defecto). Tabs: Agent Settings, Browser Settings, Run Agent, Marketplace (Deep Research), Load & Save Config. Ya incluye: chatbot + screenshots, vista de navegador, pausa/resume, stop, clear, `ask_assistant` (human-in-loop vía `CustomController`), historial JSON + GIF.

### 3.2 OpenClaw

- **Runtime**: Node 24.19.0, paquete `openclaw` 2026.9.4 (MIT).
  Dependencias relevantes: `playwright-core`, `@anthropic-ai/sdk`, `@google/genai`, `openai`, `@openclaw/ai`, `grammy` (Telegram), `croner` (scheduling), `kysely`/`node-sqlite` (estado), `clawpdf`, `jszip`, `express`, `ws`, MCP SDK (`@modelcontextprotocol/sdk`), `@trycua/cua-driver`.
- **Subsistemas detectados en `dist/`**: `agents` (embedded-agent-runner, code-mode, compaction, model-catalog, auth-profiles), `control-ui` (SPA React servida por gateway), `plugins` (registry + runtime), `gateway` (protocol + worker-environments), `state`, `worker`, `terminal-core`, `web-fetch`, `link-understanding`, `media-understanding`, `normalization-core`, `acp`, `mcp`, `telegram`, `auto-reply`, `retry`, `process`.
- **Modelos**: alojados en `models.providers.ollama` (baseUrl nativa `http://127.0.0.1:11434`, `api: ollama`). Primario `ollama/qwen3:8b` con fallbacks a qwen3:4b y qwen3:1.7b. Legado de fallback y rutas de proveedor bien definidas (`primary` + `fallbacks`).
- **Gateway**: tarea programada `OpenClaw Gateway`, port 18789, token auth, modo local loopback. Dashboard integrado en `http://127.0.0.1:18789/`.
- **Permisos**: `tools.exec.mode=full` (security=full, ask=off) configurado en openclaw.json.
- **Memoria**: plugin `memory-state`; `memory.search.enabled=false` y `rememberAcrossConversations=false`.
- **Skills**: 26 skills deshabilitadas por doctor (faltan binarios/env), 39/59 plugins habilitados.
- **UI**: control-ui (React, bundler compilado con hashes), tray WinUI, pi-tui.

## 4. Matriz de decisión por componente

Leyenda:
- **KEEP** = conservar implementación actual del Browser Agent.
- **MERGE** = integrar lo mejor de ambos detrás de una sola API.
- **PORT** = traer el concepto/patrón (no código) de OpenClaw a nuestra stack Python.
- **REIMPLEMENT** = reimplementar en nuestro stack un concepto de OpenClaw.
- **REPLACE** = sustituir la implementación actual por la de OpenClaw.
- **IGNORE** = no usar (fuera de alcance en esta fase).

| Sistema | Browser Agent | OpenClaw | Decisión |
|---|---|---|---|
| Agent core | `e2e_runner.py` + `BrowserUseAgent` (browser-use) | `dist/agents/*` (embedded runner, code-mode) | **KEEP** (nuestro núcleo estable; el de OpenClaw es Node y no automatiza navegador real como el nuestro) |
| Browser | Browser Use 0.13.10 + Playwright + Chromium (perfil persistente) | playwright-core a baja capa; relay por extensión; `web-fetch` | **KEEP** nuestro motor browser. **IGNORE** relay/extension. `web-fetch` puede PORTarse como herramienta ligera |
| Search | Solo acción `search`/navegar a motores | Proveedor de búsqueda web (Ollama/et al.) | **REIMPLEMENT**: crear `search.web(query)` como herramienta propia (Google/Bing/DDG) sobre nuestro motor |
| Forms | Acciones `click`/`input` nativas de browser-use. `form.html` en fixtures | No tiene soporte de formularios dedicado | **REIMPLEMENT** capa de formularios (detección de campos + rellenado + verificación) |
| Sessions | No persistente entre corridas (perfil Chromium sí) | Session catalog + narration + gateway sessions | **PORT** esquema de sesiones a Python; **KEEP** perfil persistente Chromium |
| Profiles | `browser_profile/` (Chromium) | auth-profiles / account profiles (channels) | **KEEP** perfiles de navegador; **PORT** nociones de perfil/cuenta si se necesitan canales |
| Memory | Solo memoria en-run + logs JSON | plugin memory-state + memory search (embeddings) | **PORT** modelo de memoria persistente (JSONL/SQLite local, embeddings opcionales vía Ollama) |
| Skills | No hay sistema | Subsistema skills (26 deshabilitadas por binarios) | **REIMPLEMENT** estructura `skills/` propia (browser, web-search, forms, files, email, research, pdf, screenshot, automation) |
| Plugins | No hay | plugin registry + SDK (39/59 activos) | **PORT** lado a lado para extensibilidad futura (no obligatorio para esta fase) |
| Tools | Registry browser-use + ACL (`ACL_ACTIONS`) | tool-metadata + tool-search (structured) | **MERGE**: registry propio con schema/validate/log/retry sobre el ACL actual |
| Email | No | Plugin de correo viejos/indirecto | **IGNORE** en esta fase (sin credenciales de correo) |
| Files | Solo lectura de logs | `@openclaw/fs-safe`, state store | **REIMPLEMENT** tools `files.read/write` acotadas a workspace |
| Screenshots | `take_screenshot`, imagen a chatbot, GIF | Sin captura dedicada | **KEEP** (nuestra implementación) |
| PDF | No | `clawpdf`, jszip | **REIMPLEMENT** tool `extract_pdf` (o PORT lógica clawpdf si tipo/MIT lo permite) |
| Downloads | `save_downloads_path` en webui | Downloads v/o comando en node | **KEEP** ruta actual; extender en browser tab |
| Human-in-loop | `ask_assistant` callback + pause/resume en webui | approvals + `operator.questions` (gateway) | **MERGE**: patrón OpenClaw de questions + nuestra UI; **KEEP** callbacks existentes |
| Permissions | ACL de acciones (browser) | `tools.exec.mode` full + operator scopes | **MERGE**: permisos por tool (schema) + ACL visto por el router |
| Scheduling | No | croner, Scheduled Task Gateway, heartbeat | **PORT** scheduling simple (cola/tareas por cron) al backend Python |
| Multi-agent | Un agente por tarea | `agents.entries` (roster) | **IGNORE** en esta fase (AGENTS.md: no multi-agente aún) |
| Model routing | Un modelo por corrida (sin fallback automático) | `primary` + `fallbacks`, provider catalog, auth profiles | **MERGE**: `MODEL ROUTER` propio con Gemini primario + Qwen3/Ollama fallback (concepto OpenClaw, motores nuestros) |
| Frontend/UI | Gradio WebUI (Ocean) | control-ui React compilado (hashes) | **KEEP** Gradio WebUI como shell (una sola app), retemado a AZUL; **IGNORE** control-ui (no rebrandable sin rebuild, y duplicaría interfaz) |
| Backend/API | Gradio app directa, sin API REST | gateway HTTP/WS (port 18789) | **MERGE**: añadir FastAPI detrás de Gradio (API única) para agent core + live events + HIL; OpenClaw gateway sigue como servicio de sistema independiente si se desea |
| Modelos (Gemini) | No presente en e2e_runner | `@google/genai`, providers | **MERGE**: Gemini como primario vía `google-genai`, Qwen3/Ollama como fallback (ya instalados) |

## 5. Decisiones con resolución de duplicación (2 impl → 1)

| Capacidad duplicada | Candidato A | Candidato B | Elección | Razón |
|---|---|---|---|---|
| Agente | Browser Agent (Python/browser-use) | OpenClaw (Node) | Browser Agent | Ya validado E2E, motor browser real, blindaje propio |
| Motor web | Browser Use + Playwright | relay/extension + web-fetch | Browser Use | Automatización completa CDP, elementos, forms, tabs |
| Fallback de modelos | No existe (A) | `primary`+`fallbacks` (B) | Patrón B implementado en A | Alineado con misión (Gemini→Qwen3/Ollama) |
| Memoria | Logs por corrida (A) | memory-state (B) | Nuevo módulo Python (patrón B) | Portar comportamiento, no runtime Node |
| Human-in-loop | callbacks webui (A) | questions/approvals (B) | Mecánica B (bloqueo con evento) + callbacks A | Se conserva UX actual añadiendo control |
| UI | Gradio (A) | control-ui React (B) | Gradio retemado azul | Un solo frontend; control-ui no es editable sin rebuild |
| Permisos | ACL acciones (A) | exec-policy/operator scopes (B) | Schema por tool dentro de A | Concepto de scopes aplicado a tools browser |

## 6. Regla fundamental cumplida (qué NO se pierde)

- ✅ No se reemplaza Browser Use ni Playwright/Chromium.
- ✅ No se pierde Qwen3/Ollama (adapter con blindaje sigue en uso).
- ✅ No se pierde Gemini (pasa a ser primario en el router nuevo).
- ✅ No se pierden pruebas E2E ni el benchmark (componente independiente, invariante).
- ✅ No se pierde el blindaje: navigation dedup, REAL_TITLE CDP, watchdog, retries controlados, JSON exclusivo.
- ✅ No se elimina código funcional; cada cambio va por rama de feature con checkpoint.

## 7. Licencias

- OpenClaw: **MIT** (`C:\Users\Cloud\...\openclaw\LICENSE`). Reutilización permitida con conservación de copyright.
- browser-use: MIT (bajo `.venv`). Playwright: Apache-2.0. Gradio: Apache-2.0.
- Política: **no copiar código** de OpenClaw en producción; diseñar equivalente (PORT) e implementarlo en nuestro stack Python, salvo fragmentos triviales tipo schema/constants donde se documentará procedencia.
- Terceros: `openclaw` incluye `THIRD_PARTY_NOTICES.md`; si alguna dependencia terminal se usa directamente, revisarla individualmente (ej. `clawpdf`, `croner`).

## 8. Orden de integración (sin salto monolítico)

```
1. Auditoría                          ✔ documentada aquí
2. Arquitectura                       ✔ bosquejo en §4-§5
3. Backend merge (API única + orquestador)
4. Browser (tabs, perfiles, sesiones persistentes)
5. Search (search.web)
6. Forms
7. Sessions / HIL
8. Memory persistente
9. Skills
10. Tools registry unificado
11. Model router (Gemini → Qwen3/Ollama fallback)
12. UI merge + tema AZUL
13. Live execution events
14. HIL UI
15. Dashboard de ejecución
16. Git (rama feature/openclaw-full-integration + checkpoints por fase)
17. Tests/E2E (existentes + nuevos)
18. Cleanup
19. Documentación final + reporte
```

Cada fase: `git diff` → compilar → unit tests → E2E.

## 9. Riesgos / limitaciones conocidas

- `control-ui` de OpenClaw es un bundle React compilado y con hash; reusarlo como UI principal exigiría reconstruir o embeber un gateway ajeno → se descarta como shell (IGNORE), se conserva el patrón visual/navegación.
- OpenClaw corre en Node; su agente core no automatiza formularios/navegación real como Browser Use → se descarta como core (KEEP nuestro).
- Gemini requiere API key; sin key el router cae a Qwen3/Ollama sin romper (fallback automático).
- El gateway de OpenClaw puede seguir funcionando en paralelo como servicio de sistema; no forma parte de la app unificada.
- Python 3.14 en `.venv` (nuevo); confirmar compatibilidad de deps antes de añadir FastAPI/websockets.