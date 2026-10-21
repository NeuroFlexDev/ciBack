# Course images and diagrams

## Image provider configuration

Images use the existing authenticated provider registry, tenant resource ledger,
bounded background queue, protected file storage and independent review policy.
No image key or signed download URL is sent to the browser. `ai_0030` must be
applied before running the new backend/workers.

For the existing VSELLM installation, an operator can configure:

```dotenv
IMAGE_PROVIDER=vsellm
IMAGE_MODEL=openai/gpt-image-2
IMAGE_API_MODE=auto
IMAGE_QUALITY=low
IMAGE_PRICE_RUB=20
IMAGE_DOWNLOAD_HOSTS=[]
```

`20` is a deliberately conservative **internal reservation**, not an asserted
provider invoice. On 2026-10-22 the official model page lists approximately
1.02 RUB for standard and 18.20 RUB for high resolution. Recheck current prices
before enabling. The application sends exactly one 1024×1024 image, explicit low
quality and no SDK retries; it freezes these options and provider origin on the
queued record. It does not infer an exact bill from approximate catalog prices.
For a single smoke test set the test organization's monthly image limit to 1
and its resource budget to the approved reservation, accounting for existing
spend. Do not change other organizations' limits.

Sources: https://vsellm.ru/docs and
https://vsellm.ru/models/openai/gpt-image-2 . Availability can be checked with
authenticated `GET /v1/models` without running a paid generation.

`auto` uses the VSELLM/native GPT Image contract (no `response_format`), or the
traditional OpenAI-compatible base64 contract for other models. `gpt_image` and
`openai` allow an explicit operator override. Unknown configuration, a missing
remote price or local-only policy denies admission. Old queued rows without
frozen options fail **unsent**, releasing monetary reservation; approved old
images remain readable.

A provider may return base64 or a signed HTTPS URL. URL delivery requires an
explicit exact hostname in `IMAGE_DOWNLOAD_HOSTS`. Each download resolves and
rejects non-public IPs, pins the validated IP while verifying hostname TLS/SNI,
forbids redirects/proxy/auth/cookies and reads at most IMAGE_MAX_BYTES+1 bytes.
Previously admitted jobs can use only the intersection of old and current
approved hosts. No URLs from course content are downloaded. PNG/JPEG/WebP are
decoded, pixel bounded and re-encoded without metadata; SVG/animation uploads
are rejected. WebP is stored as PNG.

## Public API contract

- `GET /materials/capabilities`: image formats/limits, availability,
  `image_generation_error`, `image_price_rub_upper_bound` (string or null).
- `POST /courses/{id}/images/generate` with `{prompt, request_id}` returns 202
  image metadata. A repeated identical key returns the same record; altered
  prompt/course returns 409. Poll `GET /images/{id}`.
- Terminal generated status is `pending_review`. POST `/images/{id}/review`
  with `decision: approved` is required before insertion/publication.
- Markdown references `![description](/api/images/{id}/content)`. Reading uses
  the existing author/reviewer/assignment boundaries and an authenticated blob.
- Failed/uncertain sent requests keep the original reservation and never retry
  automatically; proven queue/permission/config failures before sending release
  the reservation. The saved prompt remains available for an explicit new key.

## Diagrams

Generation prompts use fenced native Mermaid and cite the same source evidence
as surrounding text. Server artifact schemas reject unsafe or unbounded diagram
source. Full syntax is rendered locally by the frontend, never by a remote URL.
Author edits retain invalid source and show an actionable error rather than
silently dropping it. The shared browser renderer exports a flattened sanitized
SVG for PDF/ZIP. PlantUML support is explicitly a local class/sequence subset;
includes/macros/URLs and unsupported syntax fail without losing source.

## Validation and release checks

`pytest tests/test_visual_content.py tests/test_materials_release.py
tests/test_ai_access_revocation.py tests/test_protected_image_boundary.py`
exercises actual raster decoding, worker idempotency, timeout receipts, frozen
config, tenant access and URL/DNS boundaries without a paid provider.

In a real author session: create one approved-budget image, reload during queue,
confirm the same image id and one ledger reservation, review/insert/save/reload;
then view it under an assigned learner. Add a process, UML class/sequence and ER
diagram, verify errors preserve source, and export PDF/ZIP with the same visuals.
Test unknown external image references with a different tenant: access must fail.
