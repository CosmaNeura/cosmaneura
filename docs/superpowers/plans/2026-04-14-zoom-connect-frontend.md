# Zoom Connect Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken OAuth redirect "Connect Zoom" button in settings with a dialog that collects the doctor's Zoom email, calls the S2S connect endpoint, and shows a "Disconnect" button when already connected.

**Architecture:** The `ZoomConnect` component in `web/app/routes/settings.tsx` is replaced in-place. The Radix Dialog already exists in the project. The TypeScript client (`web/client/sdk.gen.ts`) already has `zoomConnect` and `zoomDisconnect` — no regeneration needed. The hey-api client returns `{ data, error }` by default (does not throw), so error handling destructures the response. After connect/disconnect, `useRevalidator().revalidate()` re-runs the `clientLoader` to refresh `zoom_connected` without a full page reload.

**Tech Stack:** React, React Router v7, react-hook-form, zod, Radix Dialog, hey-api generated client (`{ data, error }` pattern), shadcn/ui components.

---

## Files

- Modify: `web/app/routes/settings.tsx` — replace `ZoomConnect` component and update imports

---

## Task 1: Verify the Generated Client Has the Zoom Functions

**Files:**
- Read: `web/client/sdk.gen.ts`

Before touching the component, confirm the functions are there.

- [ ] **Step 1: Verify `zoomConnect` and `zoomDisconnect` exist**

```bash
grep "zoomConnect\|zoomDisconnect" web/client/sdk.gen.ts
```

Expected output (at minimum):
```
export const zoomDisconnect = ...
export const zoomConnect = ...
```

If they are missing, run `cd web && pnpm run openapi` (reads from `$OPENAPI_JSON_PATH` which is already set to `web/openapi.json`), then re-run the grep to confirm.

---

## Task 2: Replace the `ZoomConnect` Component

**Files:**
- Modify: `web/app/routes/settings.tsx`

The current `ZoomConnect` component (lines 330–373) uses the removed `/internal/v1/zoom-auth-url` endpoint. Replace the entire component and update the imports.

### Error handling pattern

The hey-api client does **not** throw by default. Every call returns `{ data, error }`:

```typescript
const { error } = await zoomConnect({ body: { email } })
if (error) {
  // error is HttpValidationError: { detail: string | ValidationError[] }
  const detail = typeof error.detail === "string"
    ? error.detail
    : "Failed to connect Zoom. Please try again."
  toast({ title: "Connection failed", description: detail, variant: "destructive" })
  return
}
```

- [ ] **Step 1: Update imports in `settings.tsx`**

The old `ZoomConnect` uses `getClient` (line 335) and `requireEnv` (line 337). Verify no other component in the file uses them:

```bash
grep -n "getClient\|requireEnv" web/app/routes/settings.tsx
```

Expected: only lines 335–338 (inside the old `ZoomConnect`). Safe to remove both.

Add these imports to the existing import block at the top of `web/app/routes/settings.tsx`:

```typescript
import { useRevalidator } from "react-router"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog"
import { Input } from "~/components/ui/input"
import { zoomConnect, zoomDisconnect } from "~/configuration/client"
```

Remove these two imports (only used by the old `ZoomConnect`):
```typescript
import { getClient } from "~/configuration/clerk"
import { requireEnv } from "~/utils/environment"
```

- [ ] **Step 2: Replace the `ZoomConnect` function**

Replace lines 330–373 (the entire `ZoomConnect` function) with:

```typescript
function ZoomConnect({ zoomConnected }: { zoomConnected: boolean }) {
  const revalidator = useRevalidator()
  const [open, setOpen] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  const formSchema = z.object({
    email: z.string().email("Please enter a valid email address"),
  })

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: { email: "" },
  })

  async function onConnect(values: z.infer<typeof formSchema>) {
    const { error } = await zoomConnect({ body: { email: values.email } })
    if (error) {
      const detail =
        typeof error.detail === "string"
          ? error.detail
          : "Failed to connect Zoom. Please try again."
      toast({ title: "Connection failed", description: detail, variant: "destructive" })
      return
    }
    toast({ title: "Zoom connected successfully" })
    setOpen(false)
    form.reset()
    revalidator.revalidate()
  }

  async function onDisconnect() {
    setDisconnecting(true)
    const { error } = await zoomDisconnect()
    if (error) {
      toast({
        title: "Failed to disconnect. Please try again.",
        variant: "destructive",
      })
      setDisconnecting(false)
      return
    }
    toast({ title: "Zoom disconnected" })
    revalidator.revalidate()
    setDisconnecting(false)
  }

  return (
    <section className="prose-headings prose w-1/2 space-y-4">
      <h3>Zoom Integration</h3>
      <p className="text-muted-foreground text-sm">
        Connect your Zoom account so Cosma can automatically join your meetings
        and generate visit notes.
      </p>

      {zoomConnected ? (
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-sm">
            <div className="h-2 w-2 rounded-full bg-green-500" />
            <span>Connected</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onDisconnect}
            disabled={disconnecting}
          >
            {disconnecting ? (
              <>
                <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                Disconnecting...
              </>
            ) : (
              "Disconnect"
            )}
          </Button>
        </div>
      ) : (
        <Dialog open={open} onOpenChange={setOpen}>
          <Button onClick={() => setOpen(true)}>Connect Zoom</Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Connect your Zoom account</DialogTitle>
              <DialogDescription>
                Enter the email address associated with your Zoom account. It
                must belong to the same Zoom organization as this app.
              </DialogDescription>
            </DialogHeader>
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onConnect)}
                className="space-y-4"
              >
                <FormField
                  control={form.control}
                  name="email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Zoom Email</FormLabel>
                      <FormControl>
                        <Input
                          type="email"
                          placeholder="you@yourorganization.com"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  disabled={form.formState.isSubmitting}
                  className="w-full"
                >
                  {form.formState.isSubmitting ? (
                    <>
                      <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    "Connect"
                  )}
                </Button>
              </form>
            </Form>
          </DialogContent>
        </Dialog>
      )}
    </section>
  )
}
```

- [ ] **Step 3: Run typecheck**

```bash
cd web && pnpm typecheck
```

Expected: no errors. If you see `error.detail` type errors, check `ZoomConnectErrors` in `web/client/types.gen.ts` — the `detail` field may be typed as `Array<ValidationError>` and you may need to adjust the string check accordingly.

- [ ] **Step 4: Commit**

```bash
git add web/app/routes/settings.tsx
git commit -m "feat: update ZoomConnect to S2S email-based connect/disconnect flow"
```

---

## Task 3: Verify in the Browser

- [ ] **Step 1: Confirm the dev server is running**

The frontend at `http://localhost:5173` (or whatever `JAVASCRIPT_SERVER_PORT` is set to) and the backend must both be running. If not, start them with `just dev` or individually with `just js_dev` and `just py_dev`.

- [ ] **Step 2: Navigate to Settings**

Open the app and go to the Settings page (`/settings`).

- [ ] **Step 3: Test connect flow**

  1. Click "Connect Zoom" → dialog opens with email input
  2. Type `notanemail` and click Connect → should show inline form error "Please enter a valid email address" (no network request sent)
  3. Type an email not in the Zoom org → should show destructive toast with the backend's error message
  4. Type the correct Zoom email → dialog closes, "Connected" indicator + "Disconnect" button appears

- [ ] **Step 4: Test disconnect flow**

  1. Click "Disconnect" → spinner appears briefly, then reverts to "Connect Zoom" button with success toast

- [ ] **Step 5: Commit any fixes found during browser testing**

```bash
git add web/app/routes/settings.tsx
git commit -m "fix: zoom connect UI corrections from browser testing"
```
