import { useCallback, useEffect, useMemo, useState } from "react"

import { Button } from "~/components/ui/button"
import { Input } from "~/components/ui/input"
import {
  type VisitPrepQuestionDraftRequest,
  type VisitPrepQuestionResponse,
  type VisitPrepQuestionSetMutationResponse,
  type VisitPrepQuestionSetResponse,
  type VisitPrepQuestionSetVersionResponse,
  visitPrepQuestionSetDefault,
  visitPrepQuestionSetList,
  visitPrepQuestionSetPublish,
  visitPrepQuestionSetReplace,
  visitPrepQuestionSetSuccessor,
} from "~/configuration/client"
import { toast } from "~/hooks/use-toast"
import { cn } from "~/lib/utils"

type VisitPrepAccess = {
  eligible: boolean
  can_manage: boolean
}

type EditorChoice = {
  value: string
  label: string
  hint: string
}

type EditorQuestion = {
  key: string
  prompt: string
  helper_text: string
  kind: VisitPrepQuestionResponse["kind"]
  choices: EditorChoice[]
  show_if: VisitPrepQuestionDraftRequest["show_if"]
}

type EditorDraft = {
  name: string
  questions: EditorQuestion[]
}

type BusyAction = "default" | "publish" | "save" | "successor" | null

function orderedQuestions(
  version: VisitPrepQuestionSetVersionResponse,
): VisitPrepQuestionResponse[] {
  return [...version.questions].sort((left, right) => {
    if (left.position !== right.position) return left.position - right.position
    return left.id.localeCompare(right.id)
  })
}

function toEditorDraft(
  version: VisitPrepQuestionSetVersionResponse,
): EditorDraft {
  return {
    name: version.name,
    questions: orderedQuestions(version).map((question) => ({
      key: question.key,
      prompt: question.prompt,
      helper_text: question.helper_text ?? "",
      kind: question.kind,
      choices: question.choices.map((choice) => ({
        value: choice.value,
        label: choice.label,
        hint: choice.hint ?? "",
      })),
      show_if: question.show_if
        ? {
            question_key: question.show_if.question_key,
            values: [...question.show_if.values],
          }
        : null,
    })),
  }
}

function normalizedText(value: string): string {
  return value.replaceAll("\r\n", "\n").normalize("NFC").trim()
}

function toRequestQuestions(
  questions: EditorQuestion[],
): VisitPrepQuestionDraftRequest[] {
  return questions.map((question) => ({
    key: question.key,
    prompt: normalizedText(question.prompt),
    kind: question.kind,
    helper_text: normalizedText(question.helper_text) || null,
    choices: question.choices.map((choice) => ({
      value: choice.value,
      label: normalizedText(choice.label),
      hint: normalizedText(choice.hint) || null,
    })),
    show_if: question.show_if
      ? {
          question_key: question.show_if.question_key,
          values: [...question.show_if.values],
        }
      : null,
  }))
}

function validateDraft(draft: EditorDraft): string | null {
  const name = normalizedText(draft.name)
  if (!name || name.length > 120) {
    return "Give this question set a name of 120 characters or fewer."
  }
  if (draft.questions.length !== 5) {
    return "A published Visit Prep must contain exactly five questions."
  }
  const narrative = draft.questions[4]
  if (narrative.kind !== "story" || narrative.show_if) {
    return "The fifth topic must always allow an open-ended patient story."
  }

  const earlierQuestions = new Map<string, EditorQuestion>()
  for (const [index, question] of draft.questions.entries()) {
    const number = index + 1
    const prompt = normalizedText(question.prompt)
    const helper = normalizedText(question.helper_text)
    if (!prompt || prompt.length > 1_000) {
      return `Question ${number} needs text of 1,000 characters or fewer.`
    }
    if (helper.length > 500) {
      return `Question ${number} helper text must be 500 characters or fewer.`
    }

    if (question.kind === "choice") {
      if (question.choices.length < 2 || question.choices.length > 8) {
        return `Question ${number} must keep between two and eight choices.`
      }
      const labels = question.choices.map((choice) =>
        normalizedText(choice.label),
      )
      if (labels.some((label) => !label || label.length > 120)) {
        return `Question ${number} has a choice label that is missing or too long.`
      }
      if (new Set(labels).size !== labels.length) {
        return `Question ${number} choice labels must be unique.`
      }
      if (
        question.choices.some(
          (choice) => normalizedText(choice.hint).length > 500,
        )
      ) {
        return `Question ${number} choice hints must be 500 characters or fewer.`
      }
    } else if (question.choices.length > 0) {
      return `Question ${number} has an invalid response format.`
    }

    if (question.show_if) {
      const target = earlierQuestions.get(question.show_if.question_key)
      const targetValues = new Set(
        target?.kind === "choice"
          ? target.choices.map((choice) => choice.value)
          : [],
      )
      if (
        !target ||
        target.kind !== "choice" ||
        question.show_if.values.length === 0 ||
        question.show_if.values.some((value) => !targetValues.has(value))
      ) {
        return `Question ${number} has an invalid display condition.`
      }
    }
    earlierQuestions.set(question.key, question)
  }

  return null
}

function initialVersionId(
  questionSets: VisitPrepQuestionSetResponse[],
): string | null {
  const allVersions = questionSets.flatMap((questionSet) =>
    questionSet.versions.map((version) => ({ questionSet, version })),
  )
  const currentDefault = allVersions.find(({ version }) => version.is_default)
  if (currentDefault) {
    const newestInDefaultLineage = [
      ...currentDefault.questionSet.versions,
    ].sort((left, right) => right.version_number - left.version_number)[0]
    return newestInDefaultLineage?.id ?? currentDefault.version.id
  }
  return (
    [...allVersions].sort(
      (left, right) =>
        right.version.version_number - left.version.version_number,
    )[0]?.version.id ?? null
  )
}

function statusLabel(version: VisitPrepQuestionSetVersionResponse): string {
  const state = version.published_at ? "Published" : "Draft"
  return version.is_default ? `${state} · Default` : state
}

function kindLabel(kind: VisitPrepQuestionResponse["kind"]): string {
  if (kind === "choice") return "Multiple choice"
  if (kind === "list") return "List answer"
  return "Written answer"
}

function mutationError(status: number | undefined, action: BusyAction): string {
  if (status === 403) return "Changes are unavailable in this session."
  if (status === 409) {
    return "This version changed elsewhere. Your edits are still here; refresh the page before trying again."
  }
  if (status === 422) {
    return "The five-question definition is not valid yet. Review the fields and try again."
  }
  if (status === 503) {
    return "Visit Prep questions are temporarily unavailable. Try again shortly."
  }
  if (action === "default") return "The default version could not be changed."
  if (action === "publish") return "This version could not be published."
  if (action === "successor") return "An editable version could not be created."
  return "This draft could not be saved."
}

function QuestionSetSettingsPanel({ canManage }: { canManage: boolean }) {
  const [questionSets, setQuestionSets] = useState<
    VisitPrepQuestionSetResponse[]
  >([])
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(
    null,
  )
  const [draft, setDraft] = useState<EditorDraft | null>(null)
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<BusyAction>(null)

  const loadQuestionSets = useCallback(async () => {
    setLoading(true)
    setLoadError(false)
    try {
      const { data } = await visitPrepQuestionSetList()
      if (!data) {
        setLoadError(true)
        return
      }
      setQuestionSets(data.question_sets)
      setSelectedVersionId((current) => {
        const currentStillExists = data.question_sets.some((questionSet) =>
          questionSet.versions.some((version) => version.id === current),
        )
        return currentStillExists
          ? current
          : initialVersionId(data.question_sets)
      })
    } catch {
      setLoadError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadQuestionSets()
  }, [loadQuestionSets])

  const selectedVersion = useMemo(
    () =>
      questionSets
        .flatMap((questionSet) => questionSet.versions)
        .find((version) => version.id === selectedVersionId) ?? null,
    [questionSets, selectedVersionId],
  )

  useEffect(() => {
    if (!selectedVersion) {
      setDraft(null)
      setDirty(false)
      return
    }
    setDraft(toEditorDraft(selectedVersion))
    setDirty(false)
    setActionError(null)
  }, [selectedVersion])

  function applyMutation(result: VisitPrepQuestionSetMutationResponse) {
    setQuestionSets((current) => {
      let found = false
      const next = current.map((questionSet) => {
        if (questionSet.id === result.question_set.id) {
          found = true
          return result.question_set
        }
        if (!result.version.is_default) return questionSet
        return {
          ...questionSet,
          versions: questionSet.versions.map((version) => ({
            ...version,
            is_default: false,
          })),
        }
      })
      return found ? next : [...next, result.question_set]
    })
    setSelectedVersionId(result.version.id)
    setDraft(toEditorDraft(result.version))
    setDirty(false)
    setActionError(null)
  }

  function updateQuestion(
    questionIndex: number,
    change: Partial<Pick<EditorQuestion, "helper_text" | "prompt">>,
  ) {
    setDraft((current) => {
      if (!current) return current
      return {
        ...current,
        questions: current.questions.map((question, index) =>
          index === questionIndex ? { ...question, ...change } : question,
        ),
      }
    })
    setDirty(true)
    setActionError(null)
  }

  function updateChoice(
    questionIndex: number,
    choiceIndex: number,
    change: Partial<Pick<EditorChoice, "hint" | "label">>,
  ) {
    setDraft((current) => {
      if (!current) return current
      return {
        ...current,
        questions: current.questions.map((question, index) =>
          index === questionIndex
            ? {
                ...question,
                choices: question.choices.map((choice, innerIndex) =>
                  innerIndex === choiceIndex
                    ? { ...choice, ...change }
                    : choice,
                ),
              }
            : question,
        ),
      }
    })
    setDirty(true)
    setActionError(null)
  }

  async function createSuccessor() {
    if (!selectedVersion || !canManage) return
    setBusyAction("successor")
    setActionError(null)
    try {
      const { data, response } = await visitPrepQuestionSetSuccessor({
        path: { version_id: selectedVersion.id },
      })
      if (!data) {
        setActionError(mutationError(response?.status, "successor"))
        return
      }
      applyMutation(data)
      toast({
        title: "Editable version ready",
        description: "The published version remains unchanged.",
      })
    } catch {
      setActionError(mutationError(undefined, "successor"))
    } finally {
      setBusyAction(null)
    }
  }

  async function saveDraft() {
    if (!selectedVersion || !draft || !canManage) return
    const validationError = validateDraft(draft)
    if (validationError) {
      setActionError(validationError)
      return
    }
    if (!selectedVersion.updated_at) {
      setActionError(
        "This draft does not have a concurrency marker. Refresh the page before saving.",
      )
      return
    }

    setBusyAction("save")
    setActionError(null)
    try {
      const { data, response } = await visitPrepQuestionSetReplace({
        path: { version_id: selectedVersion.id },
        body: {
          name: normalizedText(draft.name),
          questions: toRequestQuestions(draft.questions),
          expected_updated_at: selectedVersion.updated_at,
        },
      })
      if (!data) {
        setActionError(mutationError(response?.status, "save"))
        return
      }
      applyMutation(data)
      toast({ title: "Visit Prep draft saved" })
    } catch {
      setActionError(mutationError(undefined, "save"))
    } finally {
      setBusyAction(null)
    }
  }

  async function publishVersion(makeDefault: boolean) {
    if (!selectedVersion || selectedVersion.published_at || !canManage) return
    if (dirty) {
      setActionError("Save this draft before publishing it.")
      return
    }
    const confirmation = makeDefault
      ? "Publish this version and use it for new Visit Preps? Published versions cannot be changed."
      : "Publish this version? Published versions cannot be changed."
    if (!window.confirm(confirmation)) return

    setBusyAction("publish")
    setActionError(null)
    try {
      const { data, response } = await visitPrepQuestionSetPublish({
        path: { version_id: selectedVersion.id },
        body: { make_default: makeDefault },
      })
      if (!data) {
        setActionError(mutationError(response?.status, "publish"))
        return
      }
      applyMutation(data)
      toast({
        title: makeDefault
          ? "Question set published and selected"
          : "Question set published",
      })
    } catch {
      setActionError(mutationError(undefined, "publish"))
    } finally {
      setBusyAction(null)
    }
  }

  async function makeDefault() {
    if (!selectedVersion || !selectedVersion.published_at || !canManage) return
    setBusyAction("default")
    setActionError(null)
    try {
      const { data, response } = await visitPrepQuestionSetDefault({
        path: { version_id: selectedVersion.id },
      })
      if (!data) {
        setActionError(mutationError(response?.status, "default"))
        return
      }
      applyMutation(data)
      toast({ title: "Default Visit Prep questions updated" })
    } catch {
      setActionError(mutationError(undefined, "default"))
    } finally {
      setBusyAction(null)
    }
  }

  function discardChanges() {
    if (!selectedVersion) return
    setDraft(toEditorDraft(selectedVersion))
    setDirty(false)
    setActionError(null)
  }

  function selectVersion(version: VisitPrepQuestionSetVersionResponse) {
    setSelectedVersionId(version.id)
    setDraft(toEditorDraft(version))
    setDirty(false)
    setActionError(null)
  }

  if (loading) {
    return (
      <section className="w-full space-y-2" aria-label="Visit Prep questions">
        <h3 className="text-lg font-semibold">Visit Prep questions</h3>
        <p className="text-muted-foreground text-sm">Loading questions…</p>
      </section>
    )
  }

  if (loadError) {
    return (
      <section className="w-full space-y-3" aria-label="Visit Prep questions">
        <h3 className="text-lg font-semibold">Visit Prep questions</h3>
        <p role="alert" className="text-destructive text-sm">
          Visit Prep questions could not be loaded.
        </p>
        <Button variant="outline" size="sm" onClick={loadQuestionSets}>
          Try again
        </Button>
      </section>
    )
  }

  if (!selectedVersion || !draft) {
    return (
      <section className="w-full space-y-2" aria-label="Visit Prep questions">
        <h3 className="text-lg font-semibold">Visit Prep questions</h3>
        <p className="text-muted-foreground text-sm">
          No question sets are available for this physician.
        </p>
        {!canManage ? (
          <p className="text-muted-foreground text-sm">
            Support view is read-only and cannot create the starting set.
          </p>
        ) : null}
      </section>
    )
  }

  const editable =
    canManage && !selectedVersion.published_at && busyAction === null
  const validationError = validateDraft(draft)

  return (
    <section
      className="not-prose w-full space-y-5"
      aria-label="Visit Prep questions"
      aria-busy={busyAction !== null}
      data-sentry-mask=""
      data-ph-no-capture=""
    >
      <div className="space-y-2">
        <h3 className="text-lg font-semibold">Visit Prep questions</h3>
        <p className="text-muted-foreground max-w-3xl text-sm">
          These are the five questions patients answer before a visit. Published
          versions stay unchanged for patients already using them.
        </p>
        <p className="text-muted-foreground max-w-3xl text-xs">
          The fifth topic invites the patient’s story in their own words. You
          can ask about their medical history or what has happened since the
          last visit.
        </p>
        {!canManage ? (
          <p className="rounded-md border px-3 py-2 text-sm">
            Read-only while using support view.
          </p>
        ) : null}
      </div>

      <div className="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <nav aria-label="Question set versions" className="space-y-4">
          {questionSets.map((questionSet) => {
            const versions = [...questionSet.versions].sort(
              (left, right) => right.version_number - left.version_number,
            )
            const setName = versions[0]?.name ?? "Question set"
            return (
              <div key={questionSet.id} className="space-y-2">
                <p className="truncate text-sm font-medium" title={setName}>
                  {setName}
                </p>
                <div className="space-y-1">
                  {versions.map((version) => (
                    <button
                      key={version.id}
                      type="button"
                      aria-pressed={version.id === selectedVersion.id}
                      disabled={
                        busyAction !== null ||
                        (dirty && version.id !== selectedVersion.id)
                      }
                      onClick={() => selectVersion(version)}
                      className={cn(
                        "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors",
                        version.id === selectedVersion.id
                          ? "border-primary bg-primary/5"
                          : "hover:bg-muted",
                        "disabled:cursor-not-allowed disabled:opacity-50",
                      )}
                    >
                      <span className="block font-medium">
                        Version {version.version_number}
                      </span>
                      <span className="text-muted-foreground text-xs">
                        {statusLabel(version)}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </nav>

        <div className="min-w-0 space-y-5 rounded-lg border p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="font-semibold">
                Version {selectedVersion.version_number}
              </p>
              <p className="text-muted-foreground text-xs">
                {statusLabel(selectedVersion)}
              </p>
            </div>
            {selectedVersion.published_at ? (
              <p className="text-muted-foreground text-xs">
                Published versions are read-only.
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">
                Answer format, order, and branching remain fixed in this first
                editor.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <label
              htmlFor="visit-prep-question-set-name"
              className="text-sm font-medium"
            >
              Question set name
            </label>
            <Input
              id="visit-prep-question-set-name"
              value={draft.name}
              readOnly={!editable}
              maxLength={120}
              onChange={(event) => {
                setDraft((current) =>
                  current ? { ...current, name: event.target.value } : current,
                )
                setDirty(true)
                setActionError(null)
              }}
            />
          </div>

          <div className="space-y-4">
            {draft.questions.map((question, questionIndex) => (
              <fieldset
                key={question.key}
                className="space-y-3 rounded-lg border p-4"
              >
                <legend className="px-1 text-sm font-semibold">
                  Question {questionIndex + 1}
                </legend>
                <p className="text-muted-foreground text-xs">
                  {questionIndex === 4
                    ? "Open-ended patient story"
                    : kindLabel(question.kind)}
                </p>
                <div className="space-y-2">
                  <label
                    htmlFor={`visit-prep-prompt-${question.key}`}
                    className="text-sm font-medium"
                  >
                    Question text
                  </label>
                  <textarea
                    id={`visit-prep-prompt-${question.key}`}
                    aria-label={`Question ${questionIndex + 1} text`}
                    value={question.prompt}
                    readOnly={!editable}
                    maxLength={1_000}
                    rows={2}
                    onChange={(event) =>
                      updateQuestion(questionIndex, {
                        prompt: event.target.value,
                      })
                    }
                    className="border-input bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm shadow-sm read-only:cursor-default read-only:opacity-70 focus-visible:ring-1 focus-visible:outline-none"
                  />
                </div>
                <div className="space-y-2">
                  <label
                    htmlFor={`visit-prep-helper-${question.key}`}
                    className="text-sm font-medium"
                  >
                    Helper text
                  </label>
                  <textarea
                    id={`visit-prep-helper-${question.key}`}
                    aria-label={`Question ${questionIndex + 1} helper text`}
                    value={question.helper_text}
                    readOnly={!editable}
                    maxLength={500}
                    rows={2}
                    onChange={(event) =>
                      updateQuestion(questionIndex, {
                        helper_text: event.target.value,
                      })
                    }
                    className="border-input bg-background focus-visible:ring-ring w-full rounded-md border px-3 py-2 text-sm shadow-sm read-only:cursor-default read-only:opacity-70 focus-visible:ring-1 focus-visible:outline-none"
                  />
                </div>

                {question.kind === "choice" ? (
                  <div className="space-y-3">
                    <p className="text-sm font-medium">Choices</p>
                    {question.choices.map((choice, choiceIndex) => (
                      <div
                        key={choice.value}
                        className="grid gap-2 sm:grid-cols-2"
                      >
                        <Input
                          aria-label={`Question ${questionIndex + 1} choice ${choiceIndex + 1} label`}
                          value={choice.label}
                          readOnly={!editable}
                          maxLength={120}
                          onChange={(event) =>
                            updateChoice(questionIndex, choiceIndex, {
                              label: event.target.value,
                            })
                          }
                        />
                        <Input
                          aria-label={`Question ${questionIndex + 1} choice ${choiceIndex + 1} hint`}
                          value={choice.hint}
                          readOnly={!editable}
                          maxLength={500}
                          placeholder="Optional hint"
                          onChange={(event) =>
                            updateChoice(questionIndex, choiceIndex, {
                              hint: event.target.value,
                            })
                          }
                        />
                      </div>
                    ))}
                  </div>
                ) : null}

                {question.show_if ? (
                  <p className="text-muted-foreground text-xs">
                    Shown only for selected answers to an earlier question.
                  </p>
                ) : null}
              </fieldset>
            ))}
          </div>

          {actionError ? (
            <p role="alert" className="text-destructive text-sm">
              {actionError}
            </p>
          ) : null}

          {canManage ? (
            <div className="flex flex-wrap gap-2">
              {selectedVersion.published_at ? (
                <>
                  <Button
                    type="button"
                    onClick={createSuccessor}
                    disabled={busyAction !== null}
                  >
                    {busyAction === "successor"
                      ? "Creating…"
                      : "Edit as new version"}
                  </Button>
                  {!selectedVersion.is_default ? (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={makeDefault}
                      disabled={busyAction !== null}
                    >
                      {busyAction === "default" ? "Updating…" : "Make default"}
                    </Button>
                  ) : null}
                </>
              ) : (
                <>
                  <Button
                    type="button"
                    onClick={saveDraft}
                    disabled={
                      busyAction !== null || !dirty || Boolean(validationError)
                    }
                  >
                    {busyAction === "save" ? "Saving…" : "Save draft"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={discardChanges}
                    disabled={busyAction !== null || !dirty}
                  >
                    Discard changes
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => publishVersion(false)}
                    disabled={
                      busyAction !== null || dirty || Boolean(validationError)
                    }
                  >
                    {busyAction === "publish" ? "Publishing…" : "Publish"}
                  </Button>
                  <Button
                    type="button"
                    onClick={() => publishVersion(true)}
                    disabled={
                      busyAction !== null || dirty || Boolean(validationError)
                    }
                  >
                    {busyAction === "publish"
                      ? "Publishing…"
                      : "Publish and make default"}
                  </Button>
                </>
              )}
            </div>
          ) : null}

          {editable && dirty ? (
            <p className="text-muted-foreground text-xs">
              Save this draft before publishing or opening another version.
            </p>
          ) : null}
          {editable && validationError ? (
            <p role="status" className="text-muted-foreground text-xs">
              {validationError}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  )
}

export function VisitPrepQuestionSetSettings({
  access,
}: {
  access: VisitPrepAccess
}) {
  if (!access.eligible) return null
  return <QuestionSetSettingsPanel canManage={access.can_manage} />
}
