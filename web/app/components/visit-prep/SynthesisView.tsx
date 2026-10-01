import {
  type VisitPrepBriefItem,
  type VisitPrepPackageTopicResponse,
  type VisitPrepSynthesisResponse,
} from "~/configuration/client"

export function reportedWords(
  topic: VisitPrepPackageTopicResponse,
  initial = false,
): string {
  const kind = initial ? topic.initial_response_kind : topic.response_kind
  const answer = initial ? topic.initial_answer : topic.answer
  if (kind === "skipped") return "Skipped — not supplied"
  if (kind === null) return "Not answered"
  if (topic.kind === "choice") {
    return (
      topic.choices.find((choice) => choice.value === answer)?.label ??
      "Answer unavailable"
    )
  }
  return answer ?? "Answer unavailable"
}

function currentRef(topic: VisitPrepPackageTopicResponse): string | null {
  return (
    topic.current_revision_id ??
    topic.original_response_id ??
    topic.additional_answer_source_id
  )
}

// This is a defensive rendering boundary, not clinical semantic validation.
// Core authorizes and returns the sources and brief in one transaction.
export function validSynthesis(
  value: VisitPrepSynthesisResponse,
  id: string,
): boolean {
  const sources = value.sources
  if (
    !sources ||
    value.contract !== "visit_prep.patient_synthesis.v1" ||
    sources.contract !== "visit_prep.patient_sources.v1" ||
    sources.package_id !== id ||
    sources.preparation?.id !== id ||
    !/^[0-9a-f]{64}$/.test(sources.source_version) ||
    !Array.isArray(sources.topics) ||
    !["missing", "stale", "ready"].includes(value.status) ||
    !["not_requested", "pending", "running", "failed", "ready"].includes(
      value.build_status,
    )
  )
    return false
  if (value.status !== "ready")
    return (
      value.brief === null &&
      value.artifact_id === null &&
      value.version === null &&
      value.recorded_at === null &&
      value.build_status !== "ready"
    )
  const brief = value.brief
  if (
    !brief ||
    value.build_status !== "ready" ||
    !value.artifact_id ||
    !Number.isInteger(value.version) ||
    value.version! < 1 ||
    !value.recorded_at
  )
    return false
  const groups = [
    brief.summary,
    brief.visit_priorities,
    brief.medication_and_adherence,
    brief.barriers,
    brief.uncertainties,
  ]
  if (
    groups.some(
      (group, index) =>
        !Array.isArray(group) || group.length > [5, 3, 3, 2, 3][index],
    )
  )
    return false
  const items = [
    ...(brief.headline === null ? [] : [brief.headline]),
    ...groups.flat(),
  ]
  const answered = sources.topics.filter(
    (topic) => topic.response_kind === "answered",
  )
  if (
    (answered.length > 0 && (!brief.headline || brief.summary.length === 0)) ||
    (answered.length === 0 && items.length !== 0)
  )
    return false
  return items.every(
    (item) =>
      item &&
      typeof item.text === "string" &&
      item.text.length > 0 &&
      item.text.length <= 700 &&
      Array.isArray(item.evidence) &&
      item.evidence.length > 0 &&
      item.evidence.length <= 5 &&
      new Set(item.evidence.map((e) => e?.source_ref)).size ===
        item.evidence.length &&
      item.evidence.every((e) => {
        const topic = answered.find(
          (candidate) => currentRef(candidate) === e?.source_ref,
        )
        return (
          topic &&
          typeof e.quote === "string" &&
          e.quote.length > 0 &&
          e.quote.length <= 300 &&
          reportedWords(topic).includes(e.quote)
        )
      }),
  )
}

function CitedItem({
  item,
  topics,
}: {
  item: VisitPrepBriefItem
  topics: VisitPrepPackageTopicResponse[]
}) {
  return (
    <li className="min-w-0 space-y-1">
      <p className="[overflow-wrap:anywhere] whitespace-pre-wrap">
        {item.text}
      </p>
      <details className="text-muted-foreground text-xs">
        <summary>Supporting patient words</summary>
        {item.evidence.map((evidence) => {
          const topic = topics.find(
            (candidate) => currentRef(candidate) === evidence.source_ref,
          )!
          return (
            <figure
              key={evidence.source_ref}
              className="mt-2 space-y-1 border-l-2 pl-3"
            >
              <figcaption className="font-medium break-words">
                {topic.prompt}
              </figcaption>
              <blockquote className="[overflow-wrap:anywhere] whitespace-pre-wrap">
                {evidence.quote}
              </blockquote>
              <p className="break-all">Current source: {evidence.source_ref}</p>
            </figure>
          )
        })}
      </details>
    </li>
  )
}

export function SynthesisView({
  value,
}: {
  value: VisitPrepSynthesisResponse
}) {
  const brief = value.brief
  const topics = value.sources.topics
  const groups = brief
    ? ([
        ["Reported summary", brief.summary],
        ["Priorities for the visit", brief.visit_priorities],
        ["Medication and adherence report", brief.medication_and_adherence],
        ["Reported barriers", brief.barriers],
        ["Uncertainties to discuss", brief.uncertainties],
      ] as const)
    : []
  return (
    <section
      className="min-w-0 space-y-3 rounded-md border p-4"
      aria-labelledby="visit-prep-synthesis-title"
    >
      <h3 id="visit-prep-synthesis-title" className="text-lg font-semibold">
        Pre-visit brief
      </h3>
      <p className="text-sm font-medium">
        {value.sources.actor_role === "caregiver"
          ? "Caregiver report"
          : "Patient report"}
        {value.sources.current_answers_complete
          ? " · Answers or skips recorded for all applicable topics"
          : " · Partial contribution"}
      </p>
      <p className="text-muted-foreground text-sm">
        {value.sources.current_answers_reviewed
          ? "The recipient confirmed this version of their answers."
          : "The recipient has not confirmed this version of their answers."}{" "}
        This does not indicate clinical verification or provider review.
      </p>
      <p className="text-muted-foreground text-sm">
        AI interpretation of patient-reported information, not verified chart
        facts or clinical advice. Compare it with the recipient’s words below.
        This snapshot is from your last load.
      </p>
      {value.status === "stale" && (
        <p role="status" className="text-sm">
          The contribution changed. The earlier brief is not shown.
        </p>
      )}
      {value.status !== "ready" && (
        <p role="status" className="text-sm">
          {value.build_status === "pending"
            ? "Synthesis queued. Reload to check progress."
            : value.build_status === "running"
              ? "Synthesis is being generated. Reload to check progress."
              : value.build_status === "failed"
                ? "Synthesis is unavailable. Patient responses remain available."
                : "AI synthesis has not been generated."}
        </p>
      )}
      {value.retry_at && (
        <p className="text-muted-foreground text-sm">
          Retry not before {new Date(value.retry_at).toLocaleString()}.
        </p>
      )}
      {brief && (
        <>
          {brief.headline ? (
            <ul className="font-medium">
              <CitedItem item={brief.headline} topics={topics} />
            </ul>
          ) : (
            <p className="text-sm">
              No answers were supplied for synthesis. Skipped or missing topics
              have not been filled in.
            </p>
          )}
          {groups
            .filter(([, items]) => items.length > 0)
            .map(([label, items]) => (
              <section key={label} className="space-y-2">
                <h4 className="text-sm font-semibold">{label}</h4>
                <ul className="space-y-2 text-sm">
                  {items.map((item, index) => (
                    <CitedItem key={index} item={item} topics={topics} />
                  ))}
                </ul>
              </section>
            ))}
          <details className="text-muted-foreground text-xs">
            <summary>Synthesis version and model provenance</summary>
            <dl className="mt-2 space-y-1 break-all">
              <dt>Artifact version</dt>
              <dd>{value.version}</dd>
              <dt>Artifact</dt>
              <dd>{value.artifact_id}</dd>
              <dt>Generated</dt>
              <dd>{value.recorded_at}</dd>
              <dt>Generator / prompt / model profile</dt>
              <dd>
                {value.generator_version} / {value.prompt_version} /{" "}
                {value.model_profile}
              </dd>
            </dl>
          </details>
        </>
      )}
      {topics.some((topic) => topic.response_kind !== "answered") && (
        <p className="text-sm">
          Missing or skipped topics:{" "}
          {topics
            .filter((topic) => topic.response_kind !== "answered")
            .map(
              (topic) =>
                `${topic.prompt} (${topic.response_kind === "skipped" ? "skipped" : "not answered"})`,
            )
            .join("; ")}
        </p>
      )}
    </section>
  )
}
