# TODOs & Deuda técnica — Copper

---

## Nuevas funcionalidades

- **Acceso desde móvil**
  - Autenticación real (OAuth o similar)
  - Log de usuarios / sesiones
  - Subida de ficheros desde el móvil
  - Aprovechar compatibilidad iPhone + Mac (Handoff, Shortcuts, etc.)

- **Soporte de audio**
  - Transcripción de audio → ingestión como fuente
  - Posible integración con Whisper u otro ASR

---

## Deuda técnica

- **Redundant index in hierarchical tap** (`workflows/tap.py:_build_context`)
  En el path jerárquico, `_build_context` incluye el `index.md` del padre aunque el scanner ya lo haya procesado. Duplicación de tokens de bajo impacto por ahora; revisar si el coste de tokens se convierte en un problema a escala.

- **Scan latency en tap jerárquico** (`workflows/tap.py`)
  Cada tap sobre una mente con hijos paga una LLM call extra (el scanner). Para consultas frecuentes con un modelo lento, puede notarse. Considerar un `tap_scanner_model` override en `CopperMindConfig` (no implementar hasta que se sienta en la práctica).


---

## Resuelto

- **`_meta.md` drift + watch** (`workflows/store.py`, `core/meta.py`, `watch.py`) ✓
  Nuevo módulo `core/meta.py` con `regenerate_meta(mind, llm)`. `StoreWorkflow.run()` lo llama al final de cada store exitoso (single-chunk directo) y también refresca el padre cuando el router envía contenido a un hijo. `PolishWorkflow` usa el mismo helper. `watch.py` lo hereda gratis via `StoreWorkflow`. Guard: mente sin páginas no genera `_meta`.

- **Routing errors — `copper move`** (`cli.py`, `core/wiki.py`) ✓
  Nuevo comando `copper move <slug> --from <mente> --to <mente>` para reubicar páginas mal enrutadas. Soporta notación `padre/hijo`. Registra en el log de ambas mentes.

- **Linked minds × hierarchy** (`core/coppermind.py`) ✓
  Semántica documentada en los docstrings de `link()` y `linked_minds()`. Se emite `logger.warning` cuando se detecta un link dentro del mismo árbol (ancestro/descendiente o hermanos).

- **Recursion cap configurable por mente** (`core/coppermind.py`, `workflows/tap.py`) ✓
  Campo opcional `max_depth: int | None` en `CopperMindConfig`, con round-trip YAML limpio (la clave no aparece si es `None`). `TapWorkflow` usa el valor per-mente con fallback al global `COPPER_TAP_MAX_DEPTH`.

- **Tap context ceiling** (`workflows/tap.py`) ✓
  `TapFallbackError` lanzado cuando la recuperación falla del todo y el wiki excede `COPPER_TAP_FALLBACK_MAX_PAGES` (por defecto 50). Bajo el umbral se carga el wiki completo con warning.

- **Profiler instrumentation en tap** (`workflows/tap.py`) ✓
  `COPPER_TAP_PROFILE=true` crea un `core_utils.Profiler` real que registra tiempos por paso (retriever, scanner, descend_parallel, answer). Por defecto usa `NullProfiler` (sin coste).

- **Descenso paralelo en tap jerárquico** (`workflows/tap.py`) ✓
  `_descend_parallel` usa `ThreadPoolExecutor` para procesar hijos seleccionados concurrentemente. Cada hilo recibe un `NullProfiler` para evitar corrupción de pila. `COPPER_TAP_LEGACY_SEQUENTIAL=true` restaura el path secuencial.

---

## Diferido (fuera de scope por ahora)

- **Merge / split de sub-copperminds** — combinar dos hermanos en uno, o dividir un hijo sobredimensionado. Mayor riesgo; diferir hasta que haya casos reales.
- **Operaciones de cambio de profundidad en `deep polish`** — crear nodos de profundidad 3 mediante reorganización estructural. No implementar hasta que Fernando eleve el límite de profundidad.
- **Migración de copperminds planos existentes** — tooling para convertir una mente plana en árbol. Por ahora Fernando re-ingesta.
- **Ingestión estructural para Obsidian / texto plano** — la detección de estructura (`PDFPlugin.detect_structure`) solo está en PDF. Las notas de Obsidian con carpetas bien organizadas podrían beneficiarse de algo similar.
- **Overrides de proveedor LLM por hijo** — cada hijo es una `CopperMind` normal y hereda la semántica de config. Si se necesita un modelo distinto por hijo, ya funciona vía `.copper/config.yaml`; pero no hay CLI para setearlo al hacer `forge`.
