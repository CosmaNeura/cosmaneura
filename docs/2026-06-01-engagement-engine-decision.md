# Cosmas Engagement Engine — Brief de decisión

**Fecha:** 2026-06-01
**Autor:** Oscar (con Claude Code)
**Para:** Jefe / decisión de producto
**Estado:** Revisión técnica completa. Pendiente decisión GO / NO-GO + alcance.

---

## TL;DR

Recibimos un prototipo llamado **Cosmas Engagement Engine**: un servicio Python/FastAPI
que, a partir de texto **ya de-identificado** (transcript del chatbot de intake y/o de la
visita), estima los rasgos de personalidad **Big Five (OCEAN)** del paciente y los mapea a
un **"engagement style"** (uno de 5 estilos) con un **nivel de confianza**, para que el
clínico adapte *cómo* se comunica.

- **Calidad del código: alta.** Limpio, tipado, versionado, con tests offline, honesto
  sobre sus límites. No es un prototipo descartable.
- **No es directamente "enchufable".** Hay 3 mismatches reales con nuestro stack (proveedor
  LLM, frontend, y forma de servicio) y 1 tensión de diseño (de-identificación ↔ enlazar el
  score a la cita) que hay que resolver antes de integrar.
- **La decisión principal NO es técnica, es de producto/legal:** ¿queremos inferir y mostrar
  la *personalidad del paciente*? Es un terreno sensible. El autor del repo es el primero en
  decir que **no existe un mapeo validado** y que esto es "ayuda a la comunicación, no un
  diagnóstico".

**Recomendación:** vale la pena, pero **no** como microservicio separado. Si se aprueba,
*vendorizar* la librería dentro de `app/`, cambiar el cliente LLM a nuestro Azure OpenAI, y
exponerlo por el pipeline y el API que ya tenemos. Empezar con un **piloto interno** (no
visible al paciente, opt-in por proveedor) antes de cualquier rollout.

**Fase 0 ya corrida (2026-06-01, ver §9):** probamos el engine con nuestro gpt-4.1 sobre el
corpus público con labels. Big Five reproduce el rango de la literatura (**~0.56 macro**), así
que el swap a Azure es viable. **Pero** dos banderas: neuroticism queda en azar (0.475), y la
**confianza headline marca "Low" para casi todos los casos** por cómo está construida la
fórmula (tuneable, pero hay que decidirlo antes de mostrar nada al clínico).

---

## 1. Qué es y qué produce

Entrada: `intake_text` y/o `visit_text` (al menos uno), más un `patient_ref` opaco (no PHI).

Salida (`ScoreResult`):
- **Segmento de engagement** predicho + banda de confianza (Low/Moderate/High) + guía de
  comunicación, "drivers" y cautions por estilo.
- **Subscores Big Five** (0–100 + confianza + evidencia textual por rasgo).
- Bloque de **suficiencia de evidencia** (cuánto texto hubo) y versiones (engine / rubric /
  framework) para auditar cada score.

Los 5 estilos: **Strivers, Guided Trusters, Weighers, Caretakers, Free Spirits** (etiquetas
renombradas sobre un framework psicográfico de salud conocido; renombrar = 1 edición).

Mapeo OCEAN→estilo: cada estilo tiene un **vector prototipo OCEAN**; el perfil del paciente
se asigna al prototipo más cercano (distancia euclidiana → softmax). Transparente y tuneable.

Confianza: `separación × confianza_media_de_rasgos × factor_evidencia`. Deliberadamente
conservador: texto escaso ⇒ Low/Moderate en vez de falsa precisión.

---

## 2. Revisión de código (veredicto: sólido)

| Área | Hallazgo |
|---|---|
| Arquitectura | Capas limpias: `rubric` → `scoring` → `llm_client` → `segments` → `engine` → `api`. Núcleo puro sin red, testeable aislado. |
| Tipado/contrato | Pydantic en todo el contrato I/O; clamping/normalización robusta del JSON del modelo. |
| Parsing | Maneja code-fences, JSON embebido, valores fuera de rango y NaN con defaults seguros. |
| Tests | Suite offline completa (sin API key); CI sin secrets vía cliente heurístico. |
| Honestidad | README y docstrings reconocen explícitamente que **no hay mapeo validado** y que la precisión típica de Big-Five-desde-texto es ~0.55–0.65. |
| Privacidad (diseño) | Sin campo para identificadores; asume texto de-identificado y consentimiento upstream. |

**No encontré bugs de corrección bloqueantes.** Es código mantenible y auditable.

---

## 3. Realidad de integración con cosmaneura

El engine **no tiene base de datos** — es stateless (texto entra, score sale). Por lo tanto
"revisar la BD" significa: **dónde guardaríamos el score y de dónde sacaríamos el texto** en
*nuestra* BD. Resumen de encaje:

| Dimensión | Engine (como viene) | cosmaneura (hoy) | ¿Encaja? |
|---|---|---|---|
| Lenguaje | Python 3.10+ | Python 3.13 | ✅ |
| Framework | FastAPI | FastAPI | ✅ |
| Proveedor LLM | **Anthropic Claude** (`claude-sonnet-4-6`) | **Azure OpenAI** (`gpt-4.1`) | ❌ requiere adaptador |
| Forma de servicio | App FastAPI propia, CORS abierto, sin auth | Rutas internas con auth Clerk + jobs Celery | ❌ no correr como servicio aparte |
| Frontend | `.jsx` plano + CSS vars propias | React Router 7 + TS + Tailwind + Radix, cliente OpenAPI autogenerado | ⚠️ reescribir el panel |
| Persistencia | ninguna | SQLModel + Alembic, PKs TypeID | ➕ tabla nueva `engagement_score` |
| Fuente de texto | param `intake_text`/`visit_text` | `ai_visit_transcript` (PHI) y `ai_visit_transcript_non_phi` (de-id) | ⚠️ ver §4 |

### Forma de integración recomendada (no microservicio)
1. **Vendorizar** el paquete `cosmas_engagement` dentro de `app/` (ej. `app/engagement/`).
2. **Adaptador LLM:** implementar un `LLMClient` que use nuestro `AzureOpenAI` ya configurado
   (`app/configuration/openai.py`) en lugar del SDK de Anthropic. El engine ya define el
   protocolo `LLMClient` — es un swap de una clase, sin tocar el resto.
3. **Job Celery** (`app/jobs/engagement_scoring.py`) disparado después de que el transcript
   está listo (patrón idéntico a `summarize.py` / `anonymize.py`).
4. **Tabla nueva** `engagement_score` con FK a `appointment`/`doctor`, los 5 rasgos, el
   segmento, la confianza y las versiones (para auditar).
5. **Exponer** el score en la respuesta de `/internal/v1/notes/{id}` (ya existe).
6. **Panel** reescrito a TS + Tailwind + cliente OpenAPI autogenerado, o el `.jsx` actual
   adaptado como componente presentacional.

---

## 4. Tensión clave: de-identificación ↔ enlazar el score a la cita

El engine asume **texto de-identificado**. Nosotros tenemos dos fuentes:

- `ai_visit_transcript` (**PHI**, con FK a la cita; se borra ~30 días).
- `ai_visit_transcript_non_phi` (**de-identificado**, vía Azure DeID) — pero por diseño
  **no tiene FK a appointment/patient ni timestamps**, justamente para que no se pueda
  re-identificar.

Consecuencia: si puntuamos la **copia non-PHI**, *no podemos enlazar el score de vuelta a la
cita* para mostrarlo en la UI de la nota. Hay que elegir:

- **Opción A — puntuar el transcript PHI dentro del job de confianza** (igual que ya hace
  `summarize.py`, que manda PHI a Azure OpenAI). Mismo perímetro de exposición que hoy; el
  score se guarda enlazado a la cita. **Más simple y consistente con lo actual.**
- **Opción B — de-identificar al vuelo y puntuar, manteniendo el `appointment_id` solo en
  memoria del job**, sin tocar la tabla non-PHI. Más puro respecto a la promesa de
  de-identificación del engine, algo más de trabajo.

> Esto debe revisarlo quien lleve compliance/PHI, porque el repo *promete* de-identificación
> pero nuestro flujo de notas ya envía PHI al LLM. Hay que decidir conscientemente.

---

## 5. Tensión con la regla per-provider (restricción conocida del jefe)

Nuestra prioridad de roadmap es **customización por proveedor sin auto-migración entre
proveedores** (los cambios de prompt de un doctor no afectan a otro). El engine, en cambio,
tiene **rubric y prototipos de segmento GLOBALES versionados en código** — no por proveedor.

- El **rubric Big Five** probablemente *debe* ser global (la personalidad es universal); no
  veo razón para que cada doctor tenga su propio rubric OCEAN.
- Pero las **etiquetas de estilo / guía de comunicación / prototipos** podrían ser algo que
  un proveedor quiera ajustar. Hay que decidir si esto entra al modelo per-provider o se
  mantiene global y versionado. **No es bloqueante, es una decisión de diseño.**

---

## 6. Decisiones que necesito de ti (jefe)

1. **GO / NO-GO de producto:** ¿queremos inferir y mostrar la *personalidad del paciente*?
   Es sensible (confianza del paciente, posible exposición regulatoria/ética). El propio
   autor lo enmarca como ayuda de comunicación, no diagnóstico. → *Recomiendo GO solo como
   piloto interno opt-in.*
2. **Visibilidad:** ¿el score lo ve el clínico únicamente, o también influye en algo más?
   → *Recomiendo: solo clínico, nunca decisión clínica, con disclaimer visible.*
3. **Fuente de texto / PHI (Opción A vs B de §4):** decisión de compliance.
   → *Recomiendo Opción A (consistente con el flujo de notas actual), con visto bueno de
   quien lleve PHI.*
4. **Per-provider (§5):** ¿rubric/segmentos globales (más simple) o customizables por
   proveedor (alineado al roadmap pero más trabajo)? → *Recomiendo global v1, evaluar
   per-provider después.*
5. **Proveedor LLM:** confirmar que usamos **Azure OpenAI** (no abrir billing de Anthropic).
   → *Recomiendo sí, reusar lo que ya pagamos.*

---

## 7. Plan de implementación (si GO) — por fases

> Cada fase es un PR aislado y revisable. Estimación gruesa entre paréntesis.

- **Fase 0 — Validación (antes de codear) (S): ✅ HECHA 2026-06-01 — ver §9.** Corrimos el
  benchmark del engine con nuestro Azure OpenAI (gpt-4.1) sobre el corpus público con labels.
  Conclusión: el swap de modelo es viable (accuracy en rango de literatura), pero la confianza
  headline está mal calibrada y hay que validar sobre transcripts reales antes de seguir.
- **Fase 1 — Vendor + adaptador Azure (M):** mover `cosmas_engagement` a `app/engagement/`,
  escribir `AzureOpenAILLMClient` que implemente el protocolo `LLMClient`, portar los tests.
- **Fase 2 — Persistencia (M):** modelo `EngagementScore` (SQLModel + TypeID + FKs) +
  migración Alembic. Decidir guardado de versiones para auditoría.
- **Fase 3 — Job Celery (M):** `engagement_scoring.py` (perform/celery/queue), disparado tras
  el transcript, resolviendo la Opción A/B de §4. Idempotencia como en `summarize.py`.
- **Fase 4 — API (S):** incluir el score en `/internal/v1/notes/{id}`; regenerar cliente
  OpenAPI (`just js_generate-openapi`).
- **Fase 5 — Frontend (M):** panel en TS + Tailwind, alimentado por el loader/React Query;
  disclaimer y banda de confianza visibles; feature flag / opt-in por proveedor.
- **Fase 6 — Piloto interno (S + tiempo de observación):** activar para 1–2 doctores
  consentidos, recoger feedback, decidir rollout.

**Estimación total:** ~1.5–2.5 semanas de ingeniería para un piloto interno, sin contar
revisión legal/clínica (que va en paralelo y puede ser el camino crítico real).

---

## 8. Riesgos

- **Validez clínica:** no hay mapeo validado; precisión Big-Five-desde-texto ~0.55–0.65.
  Mitigación: banda de confianza + disclaimer + nunca decisión clínica + piloto.
- **Confianza/percepción del paciente:** "perfilado de personalidad" puede sonar invasivo.
  Mitigación: interno, opt-in, transparente.
- **Calidad con Azure OpenAI vs Claude:** el rubric/anchors se calibraron pensando en Claude;
  hay que re-validar con nuestro modelo (Fase 0).
- **Mantenimiento:** un dominio nuevo (personalidad) que mantener; versionar bien para que
  los scores históricos sean auditables (el engine ya lo soporta).

---

## 9. Fase 0 — Resultados reales con nuestro Azure OpenAI (2026-06-01)

Corrimos el benchmark del engine reemplazando el cliente Anthropic por un adaptador a
**nuestro Azure OpenAI (`gpt-4.1-2025-04-14`)** — el mismo swap que haría la Fase 1. Dataset:
corpus público Pennebaker & King (essays con label high/low por rasgo). Muestra: **n=80**,
seed 13, predicción umbralizada en 50, **0 errores de parsing**.

### Accuracy Big Five (gpt-4.1)

| Rasgo | Accuracy | F1 |
|---|---|---|
| Openness | 0.550 | 0.640 |
| Conscientiousness | 0.588 | 0.507 |
| Extraversion | 0.662 | 0.727 |
| Agreeableness | 0.537 | 0.619 |
| Neuroticism | 0.475 | 0.638 |
| **MACRO AVG** | **0.563** | **0.626** |

**Lectura:**
- ✅ **El cambio de proveedor funciona.** Con anchors/rubric calibrados para Claude, gpt-4.1
  igual aterriza en **~0.56 macro**, dentro del rango típico de la literatura (0.55–0.65).
  No hay que abrir billing de Anthropic.
- ⚠️ **Neuroticism queda en/bajo azar (0.475).** El rasgo más relevante clínicamente es el
  más flojo. Necesitaría trabajo de rubric/anchors o tratarse con cautela.
- ⚠️ **n=80, una sola seed** ⇒ los números por rasgo son ruidosos (±~0.11). El macro es más
  estable. Es un sanity check de capacidad del modelo, no una validación.

### Hallazgo crítico: la confianza headline sale "Low" casi siempre

Muestras cualitativas (output completo del engine sobre 3 essays):

| AUTHID | Segmento | Confianza | Banda | trait_conf_mean | Evidencia |
|---|---|---|---|---|---|
| 1997_518093 | Weighers | 0.13 | **Low** | 0.56 | High |
| 2000_710150 | Caretakers | 0.238 | **Low** | 0.74 | High |
| 1999_959624 | Caretakers | 0.325 | **Low** | 0.72 | High |

Aun con confianza de rasgos alta (0.56–0.74) y evidencia "High", la **confianza headline
siempre da Low**. Es por construcción: `headline = separación × trait_conf × factor_evidencia`,
y con 5 prototipos el top del softmax rara vez supera ~0.35. Para alcanzar "Moderate" (≥0.40)
haría falta una separación de ~0.62 (softmax muy picudo, raro). **Tal como viene, el dashboard
marcaría "Low confidence" para casi todos los pacientes.** Dos lecturas:
- Optimista: es honestamente conservador (coherente con "no es un instrumento validado").
- Pesimista: hace ver la feature como inútil ("¿para qué la muestro si siempre dice Low?").

Es **tuneable** — los prototipos, el `_SOFTMAX_TAU` y la fórmula de confianza son las palancas
(el propio README lo dice). Pero hay que decidir el comportamiento deseado *antes* de mostrarlo.

### Caveats importantes de esta Fase 0
- El corpus son **essays no clínicos** (stream-of-consciousness en primera persona), registro
  distinto a un transcript de intake/visita (diálogo, turnos cortos del paciente). La accuracy
  sobre *nuestros* transcripts podría ser distinta (probablemente peor). Los segmentos sobre
  essays **no son clínicamente significativos** — los autores no son pacientes.
- **Próximo paso de validación real:** correr esto sobre transcripts de-identificados de
  staging y que un clínico revise si los estilos predichos tienen sentido cualitativo. (No hay
  ground-truth de personalidad en nuestros datos, así que no se puede medir accuracy ahí — solo
  juicio cualitativo.)
- Costo: 80 llamadas a gpt-4.1, despreciable. El deployment de staging tiene cuota baja (429);
  el adaptador maneja rate limit con backoff.

### Veredicto Fase 0
**Técnicamente viable** seguir con Azure OpenAI. Pero el valor de producto depende de dos
cosas que NO son "escribir el código de integración": (1) **re-calibrar la confianza/el
mapeo** para que no diga "Low" siempre, y (2) **validar cualitativamente sobre transcripts
reales** con un clínico. Recomiendo llevarle estos números al jefe junto con las 5 decisiones
de §6 antes de invertir en Fases 1–6.

> Artefactos Fase 0 (throwaway, fuera del repo): `phase0/azure_client.py` (adaptador) y
> `phase0/run_phase0.py` (runner) dentro del zip extraído en `/tmp/cosmas-zip-review/`.

---

## 10. Validación cualitativa sobre transcripts REALES de producción (2026-06-01)

Corrimos el engine (Azure gpt-4.1) sobre una muestra aleatoria de **29 transcripts
de-identificados reales** (`ai_visit_transcript_non_phi`, 2221 en total en prod), read-only.
Como no hay ground-truth de personalidad, esto es juicio cualitativo, no accuracy.

### Resultado agregado

- **Colapso de segmentos:** de los 5 estilos, solo aparecen **2** como predicción:
  **Caretakers 62%, Guided Trusters 38%**. Strivers / Weighers / Free Spirits **nunca** ganan
  (a lo sumo aparecen como runner-up). El engine no discrimina sobre nuestros datos.
- **Confianza headline "Low" en el 100%** (rango 0.098–0.321, media 0.191). Nunca llega ni a
  Moderate (0.40). Y **no es por falta de texto**: evidencia "High" en el 100% (transcripts de
  2.000–9.500 palabras). El problema es pura falta de *separación* en el mapeo.
- **OCEAN comprimido:** casi nada puntúa "Low" en ningún rasgo. Agreeableness 26/29 High,
  Extraversion 28/29 Average, Conscientiousness 21/29 High. El modelo lee el diálogo médico
  cortés y cooperativo como uniformemente agradable/concienzudo → solo matchean los prototipos
  de alta-Agreeableness (Caretakers, Guided Trusters). De ahí el colapso.

### Causa raíz (hallazgo accionable)

1. **Mezclamos las voces de doctor y paciente.** La reconstrucción ingenua de `visit_text`
   incluye los turnos del *doctor*, que diluyen y contaminan la señal de personalidad del
   *paciente*. Una integración real debe alimentar **solo los turnos del paciente**.
2. **Registro clínico ≠ corpus de calibración.** El rubric/anchors se calibraron sobre essays
   personales; una visita médica es diálogo acotado y educado, donde la personalidad se expresa
   poco. La señal diferenciadora es débil por naturaleza del dato.
3. **La fórmula de confianza castiga la baja separación** (ya visto en §9) — sobre datos reales
   se vuelve "Low" universal, no por texto escaso sino por ambigüedad del perfil.

### Veredicto cualitativo

**Tal como viene, el engine NO es útil sobre transcripts de visita de cosmaneura:** colapsa a 2
segmentos, siempre "Low confidence", y la personalidad se lava por el registro médico + mezcla
de voces. Pero los problemas son **concretos y arreglables**, en este orden de impacto probable:

1. Alimentar **solo turnos del paciente** (requiere separación de hablante, que ya existe en
   `transcript_data.speaker`). Probablemente el cambio de mayor impacto.
2. Probar **intake_text en vez de/además de la visita** — el chatbot de intake es habla del
   paciente sin diluir, registro más libre; podría dar más señal.
3. **Re-calibrar** `_SOFTMAX_TAU`, los prototipos y la fórmula de confianza para nuestro
   dominio (no para essays).
4. Tratar el output como **muy tentativo**; con la realidad actual no es presentable al clínico.

> Esto refuerza la recomendación de §6: antes de invertir en Fases 1–6, hace falta una pasada
> de **re-calibración + alimentar solo al paciente**, y re-validar. La integración mecánica es
> la parte fácil; el valor está en esto.

> Artefacto: `phase0/validate_prod.py` (read-only sobre prod). Muestra n=29.

### Experimento A/B: ¿alimentar solo turnos del paciente lo arregla? (n=30, mismo sample)

Probamos la hipótesis #1 puntuando el **mismo** sample de dos formas: todo (doctor+paciente)
vs **solo-paciente**.

| | ALL-SPEAKER (n=30) | PATIENT-ONLY (n=29) |
|---|---|---|
| Caretakers | 43% | **62%** |
| Guided Trusters | 30% | 24% |
| Strivers | 10% | 3% |
| Weighers | 10% | 7% |
| Free Spirits | 7% | 3% |
| Confianza band | **100% Low** (mean 0.170) | **100% Low** (mean 0.204) |

**Conclusión: solo-paciente NO lo arregla.** Concentra *más* en Caretakers (62% vs 43%),
reduce la diversidad de segmentos, y la confianza sigue **100% Low** (apenas +0.03 de media,
nunca llega a Moderate). Cambió la predicción en 8/29 transcripts, pero hacia *menos* spread,
no más. La hipótesis del filtrado por hablante queda **descartada por evidencia**.

**Corrección honesta:** el "colapso a solo 2 segmentos" de la muestra anterior (n=29 sin seed)
estaba **sobre-dicho** — era varianza de muestreo. Con otra muestra, all-speaker usa los 5
estilos, pero **fuertemente sesgado** (≈73% en los dos de alta-Agreeableness). El problema real
no es "solo salen 2", es: **el modelo puntúa a casi todos alto en Agreeableness/Conscientiousness
y nunca "Low" en nada; Extraversion no tiene señal** → el softmax de 5 prototipos no puede
separar → confianza universalmente Low.

### Qué queda como lever (revisado tras el A/B)

El arreglo fácil (patient-only) no sirve. Los levers reales son más de fondo:
1. **Re-calibrar rubric + prototipos + `_SOFTMAX_TAU` para texto clínico** (no para essays). La
   compresión de rango OCEAN sobre habla médica es la causa raíz.
2. **Rediseñar la fórmula de confianza** para que no sea "Low" universal (o aceptar que el
   honesto es no mostrar un estilo cuando no hay separación).
3. **Probar `intake_text`** (habla del paciente en registro libre, sin doctor) — no testeado;
   es el único camino no descartado que podría tener más señal que la visita.
4. Hasta entonces, **no es presentable al clínico**.

---

## Anexo — referencias de archivos

- Engine (zip): `src/cosmas_engagement/{rubric,scoring,llm_client,segments,engine,schemas,version}.py`,
  `api/main.py`, `dashboard/CosmasEngagementPanel.jsx`.
- cosmaneura LLM: `app/configuration/openai.py` (Azure OpenAI, gpt-4.1).
- cosmaneura transcripts: `app/models/ai_visit_transcript.py` (PHI) y
  `app/models/ai_visit_transcript_non_phi.py` (de-id, sin FK).
- cosmaneura pipeline ejemplo: `app/jobs/summarize.py`, `app/jobs/anonymize.py`.
- cosmaneura API interno: `app/routes/internal.py` (`/internal/v1/notes/{id}`).
- cosmaneura per-provider: `app/models/note_type_customization.py`,
  `app/models/doctor_note_schema.py`.
