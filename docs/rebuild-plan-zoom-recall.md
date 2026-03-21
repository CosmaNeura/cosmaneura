# Plan de reconstruccion desde cero: Zoom + Recall + OpenAI

Fecha: 2026-03-19

## 1) Estado actual del sistema (lo que ya existe)

Este backend ya tenia una base funcional para notas clinicas, aunque el flujo principal era manual.

Flujo existente:
1. El doctor sube un archivo de audio/video manualmente.
2. Se guarda en Azure Blob.
3. Un job transcribe con AssemblyAI.
4. Se identifica hablante doctor/paciente.
5. Se genera nota clinica con OpenAI y schema del doctor.
6. Se publica en dashboard interno.

Piezas ya implementadas:
- Modelos: doctor, patient, appointment, visit_audio_file, ai_visit_transcript, ai_visit_note.
- Endpoints internos para upload y consulta de notas.
- Jobs de transcripcion y resumen.
- Intake flow ya integrado para parte de datos del paciente.

Gap principal:
- No existe aun el flujo automatico por webhooks Zoom/Recall para trabajar reunion-a-reunion sin carga manual.

## 2) Objetivo del producto (definicion funcional)

Objetivo final:
1. El doctor de MyCatholicDoctor permanece todo el dia en su sala Zoom.
2. Un bot de Recall se une automaticamente.
3. Zoom y Recall envian webhooks por cada reunion/evento.
4. El sistema identifica doctor y paciente por sesion.
5. Se guarda transcripcion + metadatos en base de datos.
6. Se procesa con OpenAI para crear historia/nota clinica.
7. La nota aparece en dashboard lista para revisar (sin matching manual).

Restriccion importante:
- Debe soportar la operacion bajo una cuenta umbrella (MyCatholicDoctor) con multiples medicos.

## 3) Arquitectura objetivo (alto nivel)

Cadena ideal:
1. Zoom webhook (`participant_joined`, `participant_left`, meeting lifecycle).
2. Capa de orquestacion de sesion (estado de la reunion).
3. Integracion Recall (join bot, job tracking, callback de transcripcion lista).
4. Persistencia de sesion + participantes + identificadores externos.
5. Pipeline de nota reutilizando jobs existentes (o adaptados) para generar `AIVisitNote`.
6. Dashboard consume appointment/note como hoy, con origen automatico.

Principios de diseno:
- Idempotencia por `event_id` y por `meeting_uuid`.
- Procesamiento async por jobs (responder webhook rapido, luego procesar).
- Trazabilidad completa (Zoom event -> Recall job -> Transcript -> Note).
- Fallback manual conservado mientras estabilizamos automatizacion.

## 4) Plan por fases (implementacion incremental)

## Fase 0: Rebootstrap local y entorno de webhooks

Objetivo:
- Recuperar capacidad de desarrollar y validar extremo a extremo en local.

Entregables:
1. Levantar app + worker + redis/postgres localmente.
2. Crear runbook de exposicion local con ngrok.
3. Definir URL de callback unica por entorno (dev/staging/prod).
4. Agregar endpoint de health para validar conectividad externa.

Criterio de exito:
- Zoom/Recall pueden hacer POST a local via ngrok y vemos eventos en logs.

## Fase 1: Capa de webhooks segura e idempotente

Objetivo:
- Recibir eventos de Zoom y Recall de forma robusta.

Entregables:
1. Nuevo router externo para webhooks:
   - `POST /external/v1/webhooks/zoom`
   - `POST /external/v1/webhooks/recall`
2. Verificacion de firma/timestamp (segun proveedor).
3. Registro de eventos recibidos para idempotencia y auditoria.
4. Cola de procesamiento async (ack inmediato 200).

Criterio de exito:
- Reenvio del mismo evento no duplica entidades.

## Fase 2: Modelo de sesion de reunion (orquestacion)

Objetivo:
- Tener una entidad canonica de reunion que conecte Zoom, Recall, doctor/paciente y appointment.

Entregables:
1. Nuevo modelo `meeting_session` con campos minimos:
   - `zoom_meeting_uuid`
   - `zoom_meeting_id`
   - `recall_bot_id`
   - `recall_job_id`
   - `doctor_id` (nullable al inicio)
   - `patient_id` (nullable al inicio)
   - `started_at`, `ended_at`
   - `status`
2. Modelo de eventos (opcional recomendado): `meeting_event_log`.
3. Reglas de correlacion para unir eventos join/left/callback en la misma sesion.

Criterio de exito:
- Una reunion produce una sola `meeting_session` consistente aunque lleguen eventos fuera de orden.

## Fase 3: Identificacion de doctor y paciente

Objetivo:
- Resolver automaticamente la relacion medico-paciente sin acciones manuales.

Estrategia incremental:
1. Doctor matching por organizador/email/metadata de Zoom.
2. Paciente matching por calendario (Acuity u otra fuente) usando ventana temporal de la reunion.
3. Fallback: si no hay match de paciente, marcar estado `needs_review` (sin bloquear pipeline).

Entregables:
1. Servicio de matching `doctor_matcher`.
2. Servicio de matching `patient_matcher`.
3. Tabla o campos de confianza (`match_confidence`) + razon de matching.

Criterio de exito:
- >= 95% de reuniones con doctor correcto y >= 90% con paciente correcto en ambiente real controlado.

## Fase 4: Ingesta de transcripcion automatica

Objetivo:
- Eliminar dependencia de upload manual para reuniones Zoom.

Decision tecnica recomendada:
- Mantener pipeline actual de notas y agregar adaptador de entrada para transcripts de Recall.

Entregables:
1. Handler de callback de Recall para transcript terminado.
2. Normalizacion al formato interno equivalente a `AIVisitTranscript.transcript_data`.
3. Asociacion a `appointment`/`meeting_session`.
4. Dispatch a job de resumen existente.

Criterio de exito:
- Transcript generado por Recall termina en `AIVisitNote` visible en dashboard automaticamente.

## Fase 5: Generacion y publicacion de nota clinica

Objetivo:
- Reusar la base actual de schemas y OpenAI minimizando cambios.

Entregables:
1. Asegurar que `summarize` funcione igual con origen Recall.
2. Definir reglas para appointment name cuando hay datos de calendario/intake.
3. Versionar prompts/outputs para poder auditar cambios de calidad.
4. Mantener flujo manual como fallback operativo.

Criterio de exito:
- Cero cambios manuales del doctor salvo revisar/editar la nota final.

## Fase 6: Hardening para produccion

Objetivo:
- Seguridad, cumplimiento, observabilidad y resiliencia.

Entregables:
1. Validacion estricta de firma de webhooks.
2. Retries con backoff e idempotencia end-to-end.
3. Politica de retencion/eliminacion de transcript acorde a compliance.
4. Monitoreo con alertas por:
   - webhook failures
   - jobs stuck
   - transcripciones sin nota final
5. Tablero operativo basico de estado de pipeline.

Criterio de exito:
- Sistema estable en carga real, con trazabilidad de cada cita.

## 5) Contratos de datos minimos (MVP)

Zoom event minimo requerido:
- `event_id`
- `event_type`
- `event_ts`
- `meeting_uuid`
- `meeting_id`
- `participant` (id, name, user_email si existe)

Recall callback minimo requerido:
- `event_id`
- `event_type`
- `bot_id`
- `job_id` o `transcript_id`
- `meeting_uuid` (o correlativo equivalente)
- `transcript` o URL de descarga

Regla:
- Si falta `meeting_uuid`, el evento se marca como no correlacionable y pasa a cola de revision.

## 6) Plan de pruebas por fase

Pruebas minimas:
1. Unit tests de parser de webhooks y firma.
2. Integration tests de correlacion de eventos out-of-order.
3. E2E local con payloads grabados (fixtures) de Zoom y Recall.
4. Smoke test real via ngrok en sandbox.
5. Test de idempotencia (mismo evento 2-3 veces).
6. Test de rollback/fallback al flujo manual.

Metrica objetivo inicial:
- Tiempo reunion fin -> nota disponible <= 10 minutos p95.

## 7) Riesgos y mitigaciones

Riesgo 1: eventos incompletos o fuera de orden.
- Mitigacion: event log + correlacion por ventana temporal + reprocessing.

Riesgo 2: matching incorrecto de paciente.
- Mitigacion: score de confianza + estado `needs_review` + reglas de calendario.

Riesgo 3: cambios de formato en payload de proveedores.
- Mitigacion: validacion por schema versionado + alertas de parse error.

Riesgo 4: dependencia de terceros (Zoom/Recall/OpenAI).
- Mitigacion: colas, retries, circuit breakers y fallback manual.

## 8) Decisiones abiertas (para cerrar antes de implementar)

1. Fuente primaria de verdad para paciente:
   - calendario (recomendado)
   - metadata Zoom
   - otro sistema
2. Recall reemplaza transcripcion de AssemblyAI o conviven ambos temporalmente.
3. Nivel de automatizacion permitido si confidence de paciente es bajo.
4. SLA interno esperado (tiempo maximo aceptable de nota).
5. Politica exacta de retencion de transcript/audio por compliance.

## 9) Orden sugerido de ejecucion (pragmatico)

1. Fase 0 + Fase 1 (webhooks confiables e idempotentes).
2. Fase 2 (meeting session canonica).
3. Fase 4 (ingesta transcript Recall) usando matching doctor/paciente temporal.
4. Fase 3 (matching fuerte con calendario).
5. Fase 5 y Fase 6 (calidad y hardening).

Con este orden recuperamos valor rapido sin bloquear por el matching perfecto desde dia uno.
