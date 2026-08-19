
---

## 8. A provider present in the data was reported as "not connected"

**Symptom** — *"tell abt 1003056227"* returned *"neither the claims datasets nor
the detection engine are currently connected"*, while *"what is risk score of
NPI 1003056227"* answered correctly for the same provider.

**Cause** — routing. The second phrasing matched a MODEL pattern and resolved
the entity; the first matched nothing (`tell abt` is not `tell me about`), fell
through to KNOWLEDGE, and answered from the corpus without querying anything.
The model then explained the empty result by asserting the sources were
disconnected — a claim it had no basis for.

**Fix** — two changes. Profile phrasing loosened, and more importantly: **an
identifier in the question now blocks the KNOWLEDGE route entirely.** An NPI
present in the question means the question is about a case, whatever words
surround it. If nothing else matches, it defaults to INVESTIGATION rather than
to the corpus.

---

## 9. Follow-up questions lost the case

**Symptom** — *"tell about the whole case"* after a successful lookup returned
the same "not connected" answer.

**Cause** — chat is stateless; each question resolved its own entity, and this
one had none.

**Fix** — `POST /api/chat` accepts `context_entity` and `context_kind` and
echoes them back. The client carries the last resolved case into the next turn,
so a follow-up attaches to it. Page context still wins when the assistant is
opened on an investigation page.

---

## 10. Components that never ran were shown as `0`

**Symptom** — *Peer benchmark: 0, Rule-based: 0* on a provider case, reading as
"checked, nothing found".

**Cause** — the orchestrator routes agents by case type, and those two are not
applicable to a provider case. Their components were rendered as zero.

**Both agents do work** — just on different case types:

| Case | billing | peer | clinical_rule |
| --- | --- | --- | --- |
| Provider | not applicable | **runs** | not applicable |
| Claim | **runs** | NPI mismatch | **runs** |

**Fix** — components whose agent did not run now report `not run`, with the
reason, and render greyed rather than as a bar at zero. The report carries the
same distinction, and states it explicitly: *"That is not the same as being
evaluated and found clean."*

The one genuine gap remains: peer comparison on **claims**, where only 3 of
8,118 claim NPIs exist in the scored provider population.

---

## 11. Reports were Markdown only

**Fix** — `backend/investigation/pdf.py` renders the same structure as PDF via
ReportLab: no browser, no headless Chrome, no system libraries, so it runs on a
laptop and in CI. `?format=md` still returns Markdown for anyone who wants to
edit or diff it.

---

## 12. The overview had no claim risk distribution

**Fix** — read from the generated claim risk table and shown beside the provider
distribution. The subtitles distinguish them: claim bands are quantile cuts
(near-equal by construction), provider bands are model output (heavily skewed).
Without that, a flat claim chart reads as a finding rather than an artefact of
how the bands were cut.
