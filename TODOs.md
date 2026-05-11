# TODOs & Deuda técnica — Copper

> Sprint de deuda técnica ejecutado siguiendo
> [`IMPLEMENTATION_PLAN_TECH_DEBT.md`](IMPLEMENTATION_PLAN_TECH_DEBT.md).
> Ver sección **Resuelto** para el detalle de lo cerrado en este sprint.

---

## Deuda activa

- **Tap context ceiling — fix de fondo** (`workflows/tap.py`)
  El cap defensivo (`COPPER_TAP_FALLBACK_MAX_PAGES`) evita explosiones de
  coste, pero no resuelve la raíz. Si un coppermind crece a >50 páginas y el
  retriever falla, hoy el tap aborta con error en vez de producir respuesta.
  *Opciones futuras:* resumen jerárquico fallback (cargar `_meta.md` + N
  primeras líneas), retriever con relax progresivo (substring matching →
  embeddings ligeros), o eliminar la rama "incluir todo" forzando al retriever
  a un top-k siempre. Diferir hasta que aparezca un coppermind real con el
  problema.

- **Tap scanner — modelo más rápido** (`workflows/tap.py`)
  La paralelización de descents (Fase C.3) ya redujo la latencia user-facing.
  Si tras la comparativa A/B real (paso C.4 del plan) el scanner sigue siendo
  cuello de botella, considerar `tap_scanner_model` override en
  `CopperMindConfig` para bajar el scanner a un modelo haiku-class. Riesgo:
  divergencia de calidad si el scanner pequeño elige hijos mal. Implementar
  con guardrail (modo strict que corra el scanner grande de fondo en N% de
  queries y compare).

- **Index redundante en hierarchical tap** (`workflows/tap.py:_build_context`)
  En el path jerárquico, `_build_context` incluye el `index.md` del padre
  aunque el scanner ya lo haya procesado. Duplicación de tokens de bajo
  impacto. Diferir hasta que el coste de tokens se sienta a escala.

- **Validación A/B del descent paralelo** (seguimiento de Fase C.4)
  La paralelización ya está activa por defecto, con
  `COPPER_TAP_LEGACY_SEQUENTIAL` como escape hatch. Falta validar en un
  coppermind real que la calidad de respuesta no regresa. Tras 1-2 versiones
  de uso real sin incidencias, retirar el flag legacy.

---

## Resuelto (sprint deuda técnica)

- **`_meta.md` drift + watch** (`workflows/store.py`, `core/meta.py`, `watch.py`) ✓
  Nuevo módulo `core/meta.py` con `regenerate_meta(mind, llm)`.
  `StoreWorkflow.run()` lo llama al final de cada store exitoso (single-chunk
  directo) y también refresca el padre cuando el router envía contenido a un
  hijo. `PolishWorkflow` usa el mismo helper. `watch.py` lo hereda gratis vía
  `StoreWorkflow`. Guard: mente sin páginas no genera `_meta`.

- **Routing errors — `copper move`** (`cli.py`, `core/wiki.py`) ✓
  Nuevo comando `copper move <slug> --from <mente> --to <mente>` para
  reubicar páginas mal enrutadas. Soporta notación `padre/hijo`. Registra en
  el log de ambas mentes.

- **Linked minds × hierarchy** (`core/coppermind.py`) ✓
  Semántica documentada en los docstrings de `link()` y `linked_minds()`. Se
  emite `logger.warning` cuando se detecta un link dentro del mismo árbol
  (ancestro/descendiente o hermanos).

- **Recursion cap configurable por mente** (`core/coppermind.py`, `workflows/tap.py`) ✓
  Campo opcional `max_depth: int | None` en `CopperMindConfig`, con
  round-trip YAML limpio (la clave no aparece si es `None`). `TapWorkflow`
  usa el valor per-mente con fallback al global `COPPER_TAP_MAX_DEPTH`.

- **Tap context ceiling — cap defensivo** (`workflows/tap.py`) ✓
  `TapFallbackError` lanzado cuando la recuperación falla del todo y el wiki
  excede `COPPER_TAP_FALLBACK_MAX_PAGES` (por defecto 50). Bajo el umbral se
  carga el wiki completo con warning. *El fix de fondo sigue en deuda activa.*

- **Profiler instrumentation en tap** (`workflows/tap.py`) ✓
  `COPPER_TAP_PROFILE=true` crea un `core_utils.Profiler` real que registra
  tiempos por paso (retriever, scanner, descend_parallel, answer). Por defecto
  usa `NullProfiler` (sin coste).

- **Descenso paralelo en tap jerárquico** (`workflows/tap.py`) ✓
  `_descend_parallel` usa `ThreadPoolExecutor` para procesar hijos
  seleccionados concurrentemente. Cada hilo recibe un `NullProfiler` para
  evitar corrupción de pila. `COPPER_TAP_LEGACY_SEQUENTIAL=true` restaura el
  path secuencial.

- **Mejor logging de orphan markers** (`workflows/store.py`) ✓
  El warning de orphan-drop ahora incluye nombre de entidad y keywords inline
  (`"Yu-Thorak" — keywords: [yu-thorak, gargantuan, ...]`) para diagnóstico
  sin necesidad de cruzar con los logs de extracción anteriores.

- **Parser XML relajado + retries diferenciados** (`workflows/store.py`) ✓
  `_normalize_xml` elimina fences markdown y smart quotes. `_parse_wiki_pages`
  admite atributos en cualquier orden y auto-cierra páginas truncadas con
  warning. `_send_with_retry` distingue respuesta vacía (hint "sé conciso") de
  XML malformado (hint estructural estricto). `_MAX_XML_RETRIES` subido a 2.

- **Carry-over de visual markers entre ingots** (`workflows/store.py`) ✓
  Marcadores no colocados en el ingot N se transfieren al N+1 en lugar de
  descartarse. Buffer acotado a 20 markers. Al último ingot, los residuales
  emiten el warning de orphan-drop mejorado. Corrige el caso Yu-Thorak donde
  el chunker separaba marker y contenido en ingots adyacentes.

- **UI: vista de wiki en árbol completo** (`api/routes/minds.py`, `api/templates/index.html`) ✓
  Nuevo endpoint `GET /minds/{name:path}/wiki/tree` devuelve páginas agrupadas
  por mente en pre-order DFS (`{mind, depth, slugs}`). La UI usa este endpoint
  y muestra secciones con indentación y prefijo `└─`. `showPage()` acepta
  `mindPath` explícito para abrir y guardar páginas de mentes hijas.

- **Parser fix — overwrites destructivos sobre páginas existentes** (`workflows/store.py`) ✓
  El parser relajado de Fase D producía dos modos de corrupción al sobrescribir
  páginas existentes: (1) cuando el LLM omitía los tags `<content>` el body quedaba
  vacío y la upsert wipeaba la página; (2) cuando la respuesta se truncaba
  mid-`<content>`, el auto-close devolvía un body parcial que reemplazaba al
  contenido completo. Fix: `_parse_wiki_pages` skipea cuerpos vacíos y propaga
  `was_auto_closed: bool`; `_apply_wiki_updates` rechaza upserts truncados sobre
  páginas existentes (preserva contenido aunque sea stale). Páginas nuevas siguen
  aceptando best-effort. Regresión observada en Yu-Thorak en re-ingestas.

---

## Diferido (fuera de scope por ahora)

- **Merge / split de sub-copperminds** — combinar dos hermanos en uno, o
  dividir un hijo sobredimensionado. Mayor riesgo; diferir hasta que haya
  casos reales.
- **Operaciones de cambio de profundidad en `deep polish`** — crear nodos de
  profundidad 3 mediante reorganización estructural. No implementar hasta que
  Fernando eleve el límite de profundidad.
- **Migración de copperminds planos existentes** — tooling para convertir una
  mente plana en árbol. Por ahora Fernando re-ingesta.
- **Ingestión estructural para Obsidian / texto plano** — la detección de
  estructura (`PDFPlugin.detect_structure`) solo está en PDF. Las notas de
  Obsidian con carpetas bien organizadas podrían beneficiarse de algo
  similar.
- **Overrides de proveedor LLM por hijo (CLI)** — cada hijo es una
  `CopperMind` normal y hereda la semántica de config. Si se necesita un
  modelo distinto por hijo, ya funciona vía `.copper/config.yaml`; pero no
  hay CLI para setearlo al hacer `forge`.

---

## Nuevas funcionalidades (versiones posteriores de Copper)

> No prioritarias hoy. Se mantienen aquí como roadmap.

- **Acceso desde móvil**
  - Autenticación real (OAuth o similar)
  - Log de usuarios / sesiones
  - Subida de ficheros desde el móvil
  - Aprovechar compatibilidad iPhone + Mac (Handoff, Shortcuts, etc.)
  - *Atajo low-cost:* exponer la API actual vía Tailscale/Cloudflare tunnel +
    Shortcut iOS. OAuth completo y multi-tenant es trabajo de semanas.

- **Soporte de audio**
  - Transcripción de audio → ingestión como fuente
  - Posible integración con Whisper u otro ASR
  - *Nota:* encaja como un `IngestPlugin` más, opcional como `pdf`.
