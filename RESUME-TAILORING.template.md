# Resume Tailoring — Agent Instructions (template)

Copy this to your own spec (the path in `config/profile.yaml` → `resume.tailoring_spec`),
then fill in the bracketed parts with your real details. The Applier delegates all resume
tailoring to whatever this file says — it does not invent its own logic.

Standing workflow: a JD comes in → you produce a tailored, ATS-clean, **one-page** resume,
log it, and hand back the PDF.

---

## 0. Standing authorization & hard rules

- **Tailoring is pre-authorized.** Copying the base, editing a variant, and compiling need
  no per-JD confirmation.
- **NEVER edit the base template** (`resume.work_dir`/`<BaseName>.tex` and its PDF). It is
  the source of truth; every variant is a copy.
- **NEVER fabricate.** Reorder, reframe, and re-weight real experience and use the JD's
  exact vocabulary where truthful. Do NOT invent skills, tools, domains, or metrics the
  candidate lacks. If the JD wants something they lack, say so — don't paper over it. No
  keyword-stuffing, no hidden/white text.
- **Trim skills the JD does not call for.** A full skills block reads as overclaiming.
  Remove, never pad.
- **Keep every resume to ONE PAGE.**
- **Confirm before** any non-tailoring edit to the candidate's documents, or anything
  destructive.

## 1. Eligibility gate — do this FIRST, before building

Screen the JD for hard blockers before spending effort. Fill in the candidate's real
constraints (citizenship, work authorization, location, hard no-go categories):

| Gate in JD | Verdict |
|---|---|
| Requires citizenship / security clearance the candidate lacks | **Ineligible.** Flag, do NOT build, log a ✗ row. |
| Location / enrollment radius the candidate is outside | **Ineligible.** Flag, don't build. |
| "No visa sponsorship" (when relevant to the candidate) | Soft or hard per their status — flag and confirm. |
| A category the candidate hard-refuses (see `config/preferences.yaml`) | **Ineligible.** Flag, don't build. |
| Openly sponsors / strong fit | Call it out as a plus. |

If a hard gate applies: say so clearly, **stop**, and log a `✗ INELIGIBLE` row in the tracker.

## 2. Read the bench before choosing what to feature

The candidate's full project/experience bench lives at `resume.bench` (e.g. a notes vault).
**Read the relevant pages across the whole bench and all its subfolders** before deciding —
don't tailor only from what's already on the base resume. Swap projects/experience in and
out to fit the JD.

Keep a short list here of the candidate's strongest items and which role-types each fits, so
selection is fast. `[FILL IN: your experience + project bench, and the role-type each maps to.]`

## 3. Category folder — REUSE before creating

Each tailored resume lives at `resume.work_dir/<category>/<BaseName>.{tex,pdf}`. Base stays
in the parent dir, untouched.

- **Reuse first.** If the JD fits an EXISTING category folder, reuse it rather than spawning
  a near-duplicate. One resume can serve multiple same-category companies.
- **New folder only** for a genuinely new category, OR when tailoring for this JD would pull
  a shared resume away from the companies it already serves (split-on-divergence).

Keep the list of existing category folders here and update it as you add.
`[FILL IN: your category folders, e.g. swe / ml / fullstack / backend / ai …]`

## 4. Tailor

- Reorder/reframe **Experience** bullets so the most JD-relevant lead; adopt the JD's exact
  nouns where truthful.
- Swap **Projects** to the best-fit set from the bench; retitle project headings toward the
  role where honest.
- Reorder **Skills** to lead with the JD's named languages/tools; **delete** skills the JD
  does not call for.
- Trim the least-relevant bullet(s) to hold one page.

## 5. Compile & verify

```bash
cd <resume.work_dir>/<category>
pdflatex -interaction=nonstopmode -halt-on-error <BaseName>.tex
# confirm the log says: Output written ... (1 page ...)
rm -f <BaseName>.aux <BaseName>.log <BaseName>.out
```
Use whatever LaTeX toolchain is installed. If the template includes `\input{glyphtounicode}`
+ `\pdfgtounicode=1`, text extracts cleanly (ATS-safe). Sanity-check with `pdftotext -layout`.

## 6. Log the tracker row

Add/point a row in the packages tracker (`config/profile.yaml` → `free_text.packages_tracker`):

`| Company | Role | Location | Term | <category>/ | [ ] | Notes (fit + any eligibility flag) |`

- **Never** check the `Applied` box without explicit confirmation.
- If the candidate's tracker is inside a notes vault they don't want auto-committed, do not
  `git add`/commit it.

## 7. Deliver

Send the PDF. In chat: summarize what changed, name the **honesty guardrails** (what you did
NOT claim and why), and give a **candid fit read** — for stretch roles, name the gaps plainly.

## Quick facts (fill in)
`[FILL IN: citizenship / work-authorization status, degree + school + grad date, location,
standout strengths, and weakest-fit areas — so the eligibility gate and framing are accurate.]`
