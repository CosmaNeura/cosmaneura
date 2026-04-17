# Cosmas — Zoom App Marketplace Submission Package

Everything you need to publish Cosmas to production on the Zoom App Marketplace.

---

## What's in this package

| File | Purpose | Where it goes |
|---|---|---|
| `cosmas-icon-1024.png` | Master app icon (1024 × 1024 PNG, transparent-edge rounded square) | Upload to Zoom → App Listing → **App Icon** |
| `cosmas-icon-512.png` | Downscaled icon (matches Zoom's typical 512 spec) | Backup if 1024 is rejected |
| `cosmas-icon-256.png`, `cosmas-icon-128.png`, `cosmas-icon-64.png` | Fallback / favicon sizes | Host on your site as `/favicon-128.png`, etc. |
| `cosmas-banner-1824x176.png` | Marketplace banner (exactly 1824 × 176 PNG) | Upload to Zoom → App Listing → **Banner Image** |
| `terms-of-service.html` | Hostable TOS page | Publish at `https://cosmaneura.com/terms-of-service.html` (or similar) |
| `privacy-policy.html` | Hostable Privacy Policy | Publish at `https://cosmaneura.com/privacy-policy.html` |
| `support.html` | Hostable support contact page | Publish at `https://cosmaneura.com/support.html` |

All three HTML pages are self-contained (single file, external fonts loaded from Google Fonts via CDN) — no build step, no dependencies. Drop them on your web host and they work.

---

## Step-by-step: filling in Zoom's submission form

### 1. Before you publish

- [ ] Make sure **all code is on `main`** (Zoom requires production code on your default branch before they approve).
- [ ] Host the three HTML pages on a public URL. Use `https://cosmaneura.com/…` or a subdomain (`https://app.cosmaneura.com/…`) — whatever matches your existing site setup.
- [ ] Make sure each page loads without authentication (no login wall, no geo-block).
- [ ] Copy `cosmas-icon-128.png` up to the site root so the `<link rel="icon">` reference on each page resolves.

### 2. Fields Zoom will ask for

| Zoom field | What to enter |
|---|---|
| **App name** | `Cosmas` |
| **Short description** | `The AI medical scribe for Zoom visits. Dignified clinical documentation for private-practice physicians.` |
| **Long description** | See the block below — paste as-is or edit. |
| **Company name** | `CosmaNeura, Inc.` |
| **Company URL** | `https://cosmaneura.com` |
| **App icon** | Upload `cosmas-icon-1024.png` (fallback: `cosmas-icon-512.png`) |
| **Banner** | Upload `cosmas-banner-1824x176.png` |
| **Category** | Likely **Business** or **Education** depending on Zoom's taxonomy; Healthcare is sometimes a sub-category. Pick the one that best fits your GTM — you can change it later. |
| **Privacy Policy URL** | `https://cosmaneura.com/privacy-policy.html` (or wherever you host it) |
| **Terms of Service URL** | `https://cosmaneura.com/terms-of-service.html` |
| **Support URL** | `https://cosmaneura.com/support.html` |
| **Support contact email** | `info@cosmaneura.com` |
| **Technical contact email** | `dev@cosmaneura.com` |
| **Documentation URL** | Optional. If you have public docs, point here; otherwise reuse the support URL. |

### 3. Suggested long description

> Cosmas is an AI-assisted clinical documentation assistant built for physicians in private practice. After you authorize Cosmas once, it quietly joins your scheduled Zoom visits as a participant, transcribes the conversation, and produces a draft SOAP note ready for your review and sign-off — so you can finish each visit and be present with the next patient, instead of charting.
>
> Cosmas is built by CosmaNeura, Inc. on the principle that clinical AI should preserve the dignity of the patient-physician relationship. Our data practices reflect that: we never sell or monetize your content, a Business Associate Agreement is required before any PHI is processed, and you can opt out of product-improvement use at any time.
>
> **Who it's for:** solo and small-group private-practice physicians, especially those running telehealth visits on Zoom.
>
> **What it does today:** auto-joins Zoom visits, transcribes, and drafts SOAP notes for clinician review.
>
> **Requirements:** a HIPAA-eligible Zoom plan with a BAA executed between you and Zoom; a signed BAA with CosmaNeura before processing PHI.

### 4. Scopes / permissions justification

When Zoom asks *why* you need each scope, keep the answer tied to the user-visible feature. A template:

- `meeting:read` / `meeting:read:admin` — to identify the scheduled visit the user wants Cosmas to join.
- `meeting:write` — to invite the Cosmas bot as a participant.
- `recording:read` — to access the post-meeting artifact for transcription and note generation.
- `user:read` — to associate meetings with the authorizing clinician's account.

Tailor the exact scope list to what your production build actually requests; don't request scopes you don't use (reviewers will flag it).

### 5. Demo video / walkthrough

Zoom's review team usually asks for a short screen-recorded walkthrough of the install flow, consent prompt, and one end-to-end use (meeting → note). You don't need a polished marketing video — a clear 2–3 minute Loom is fine.

---

## Things you still need before going live

These are outside what I can produce from text alone, but you'll want them queued up for your Zoom reviewer:

1. **Legal review.** The TOS and Privacy Policy are drafted to be defensible for a HIPAA-adjacent clinical product and tuned to your exact data-use ask (improve AI + build features; no monetize/sell). They are **not a substitute for counsel.** At minimum have a healthcare-tech attorney skim:
   - TOS §§ 5 (HIPAA), 6 (data & AI), 13 (liability cap), 17 (arbitration)
   - Privacy §§ 4 (HIPAA), 5 (AI training), 6 (sharing), 8 ("what we do not do")
2. **BAA template.** Referenced throughout the TOS and Privacy Policy. If you don't have one finalized, tell Zoom's reviewer "BAA available on request at info@cosmaneura.com." Drata has a solid template you can start from given your Microsoft for Startups / Drata path.
3. **Subprocessor list.** Privacy § 7 says the current list is available on request. Keep a short doc listing at minimum: Zoom (video), Recall.ai (meeting capture), your AI inference provider(s), your cloud host, and any other vendor that touches meeting content. Zoom reviewers sometimes ask.
4. **Data-flow diagram.** Useful for the security-review portion of Zoom's marketplace review. A simple arrow diagram showing Zoom → Recall.ai (with BAA) → Cosmas backend → AI inference → clinician UI is usually sufficient.
5. **SOC 2 Type I status.** You're already pursuing this with Drata. Mention it in your description and, if possible, share the report (or an in-progress letter) with Zoom's reviewer.

---

## Editing notes

All three HTML pages use CSS variables at the top of the `<style>` block. If you want to tweak the palette later:

```css
--navy-deep: #0B2341;   /* primary */
--navy-mid:  #18365C;
--gold:      #C9A961;   /* accent */
--gold-bright: #E0BF74;
--cream:     #F5F0E4;   /* light accent */
```

The pages share the same design system, so any palette or typography change should be applied to all three to keep the set consistent.

---

*Generated April 16, 2026 for CosmaNeura, Inc.*
