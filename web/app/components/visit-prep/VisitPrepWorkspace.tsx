import { useEffect, useState } from "react"

import { Button } from "~/components/ui/button"
import {
  type PackagePayload,
  type PrepPayload,
  providerJson,
} from "~/lib/visit-prep-api"

type QuestionSet = {
  id: string
  question_set_id: string
  name: string
  is_default: boolean
  questions: Array<{
    key: string
    prompt: string
    show_if: { question_key: string; values: string[] } | null
  }>
}

function defaultSchedule() {
  const at = new Date(Date.now() + 48 * 3600_000)
  const p = (n: number) => String(n).padStart(2, "0")
  const local = `${at.getFullYear()}-${p(at.getMonth() + 1)}-${p(at.getDate())}T${p(at.getHours())}:${p(at.getMinutes())}:00`
  const off = -at.getTimezoneOffset(),
    abs = Math.abs(off)
  return `${local}${off >= 0 ? "+" : "-"}${p(Math.floor(abs / 60))}:${p(abs % 60)}`
}

export function VisitPrepWorkspace() {
  const [sets, setSets] = useState<QuestionSet[]>([])
  const [preps, setPreps] = useState<PrepPayload[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [pkg, setPkg] = useState<PackagePayload | null>(null)
  const [link, setLink] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [firstName, setFirstName] = useState("Synthetic")
  const [email, setEmail] = useState("patient@example.test")

  async function refresh() {
    const [q, p] = await Promise.all([
      providerJson<{ question_sets: QuestionSet[] }>(
        "/visit-prep/question-sets",
      ),
      providerJson<{ preparations: PrepPayload[] }>("/visit-prep/preps"),
    ])
    setSets(q.question_sets)
    setPreps(p.preparations)
  }
  useEffect(() => {
    refresh().catch(() =>
      setError("Visit Prep is unavailable for this account."),
    )
  }, [])

  async function ensureDefault() {
    await providerJson("/visit-prep/question-sets/default", { method: "POST" })
    await refresh()
  }
  async function createPrep() {
    const version = sets.find((s) => s.is_default) ?? sets[0]
    if (!version) {
      setError("Publish the default question set first.")
      return
    }
    const created = await providerJson<PrepPayload>("/visit-prep/preps", {
      method: "POST",
      json: {
        idempotency_key: crypto.randomUUID(),
        recipient: {
          first_name: firstName,
          role: "patient",
          email,
          phone: "+15555550100",
        },
        physician_display_name: "Dr Synthetic",
        scheduled_at: defaultSchedule(),
        timezone_name:
          Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        question_set_version_id: version.id,
      },
    })
    setLink(created.access_url)
    await refresh()
  }
  async function copyLink(prepId: string) {
    const rotated = await providerJson<PrepPayload>(
      `/visit-prep/preps/${prepId}/copy-link`,
      { method: "POST" },
    )
    setLink(rotated.access_url)
    if (rotated.access_url)
      await navigator.clipboard.writeText(rotated.access_url)
  }
  async function openPackage(prepId: string) {
    setSelected(prepId)
    setPkg(
      await providerJson<PackagePayload>(`/visit-prep/preps/${prepId}/package`),
    )
  }

  const current = sets.find((s) => s.is_default) ?? sets[0]
  return (
    <div
      className="mx-auto max-w-3xl space-y-8 p-6"
      data-testid="visit-prep-workspace"
    >
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Visit Prep</h1>
        <p className="text-muted-foreground text-sm">
          Synthetic link-only journey. Email/SMS send stays off.
        </p>
        {error && <p className="text-sm text-red-700">{error}</p>}
      </header>
      <section className="space-y-3" aria-labelledby="vp-questions">
        <h2 id="vp-questions" className="text-lg font-medium">
          Question set
        </h2>
        <Button
          onClick={() => ensureDefault().catch((e) => setError(String(e)))}
        >
          Ensure founder five
        </Button>
        <ol className="list-decimal space-y-2 pl-5 text-sm">
          {(current?.questions ?? []).map((q) => (
            <li key={q.key}>
              <span className="font-medium">{q.prompt}</span>
              {q.show_if && (
                <span className="text-muted-foreground">
                  {" "}
                  (if {q.show_if.question_key} in {q.show_if.values.join(", ")})
                </span>
              )}
            </li>
          ))}
        </ol>
      </section>
      <section className="space-y-3" aria-labelledby="vp-create">
        <h2 id="vp-create" className="text-lg font-medium">
          Create copyable HTTPS link
        </h2>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-sm">
            Synthetic first name
            <input
              className="mt-1 w-full rounded border px-3 py-2"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Synthetic email
            <input
              className="mt-1 w-full rounded border px-3 py-2"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
        </div>
        <Button onClick={() => createPrep().catch((e) => setError(String(e)))}>
          Create Visit Prep
        </Button>
        {link && (
          <p
            className="bg-muted/40 rounded border p-3 text-sm break-all"
            data-testid="vp-link"
          >
            {link}
          </p>
        )}
      </section>
      <section className="space-y-3" aria-labelledby="vp-list">
        <h2 id="vp-list" className="text-lg font-medium">
          Preparations
        </h2>
        <ul className="space-y-2">
          {preps.map((prep) => (
            <li
              key={prep.id}
              className="flex flex-wrap items-center gap-2 rounded border p-3 text-sm"
            >
              <span className="font-medium">{prep.lifecycle_state}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => copyLink(prep.id)}
              >
                Copy HTTPS link
              </Button>
              <Button
                size="sm"
                onClick={() =>
                  openPackage(prep.id).catch((e) => setError(String(e)))
                }
              >
                Review package
              </Button>
              <span className="text-muted-foreground">
                send email={String(prep.can_send_email)} sms=
                {String(prep.can_send_sms)}
              </span>
            </li>
          ))}
        </ul>
      </section>
      {pkg && selected && (
        <section
          className="space-y-4"
          aria-labelledby="vp-package"
          data-testid="vp-package"
        >
          <h2 id="vp-package" className="text-lg font-medium">
            Package review
          </h2>
          <p className="text-sm">
            Raw answers always available:{" "}
            {String(pkg.raw_answers_always_available)}
          </p>
          <div className="space-y-3">
            <h3 className="font-medium">Verbatim answers</h3>
            {pkg.topics.map((t) => (
              <article
                key={t.question_key}
                className="rounded border p-3 text-sm"
              >
                <h4 className="font-medium">{t.prompt}</h4>
                <p className="mt-2 whitespace-pre-wrap">
                  {t.response_kind === "skipped"
                    ? "Skipped — not supplied"
                    : (t.answer ?? "Not answered")}
                </p>
                <p className="text-muted-foreground mt-2 text-xs">
                  Recorded: {t.recorded_at ?? "n/a"}
                </p>
              </article>
            ))}
          </div>
          <div className="space-y-2 rounded border p-3 text-sm">
            <h3 className="font-medium">Synthesis (alongside raw)</h3>
            <p>Status: {pkg.synthesis.status}</p>
            {pkg.synthesis.brief.headline && (
              <p>{pkg.synthesis.brief.headline.text}</p>
            )}
            <ul className="list-disc pl-5">
              {pkg.synthesis.brief.summary.map((item, i) => (
                <li key={i}>{item.text}</li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  )
}
