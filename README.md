# Udyam Saathi

Team Signal · Vellore Institute of Technology (VIT), Vellore · Airtel SYNC 2026

Udyam Saathi is a local sandbox for the fictional Accel Business case. The owner can describe business needs, confirm an eligible quote, verify synthetic documents, request partner help, and track a simulated order. The app uses React, TypeScript, i18next, FastAPI, Pydantic, PostgreSQL, Keycloak, private S3-compatible SeaweedFS storage, PaddleOCR, and a local Qwen model through Ollama.

## Start the sandbox

Run PowerShell from this directory with Docker Desktop running.

```powershell
docker compose up --build -d
docker compose exec -T model ollama pull qwen2.5:1.5b
docker compose logs -f app
```

Open [the app](http://127.0.0.1:8174/), [API docs](http://127.0.0.1:8174/docs), or [health status](http://127.0.0.1:8174/api/health). The first build downloads images and model weights. The first OCR check initializes its models. The guided form works during advisor setup.

The supplied dataset mounts read-only. Keep the source under the original workspace or set `$env:SAATHI_DATA_PATH` to the absolute dataset directory before Compose starts. Only the app and sign-in ports are mapped to loopback. `docker compose stop` stops the app and keeps local cases.

| Synthetic user | Password | Purpose |
|---|---|---|
| `owner-try` | `saathi-owner-try` | Fresh owner case for your walkthrough |
| `owner-demo` | `saathi-owner-demo` | Recorded walkthrough |
| `partner-demo` | `saathi-partner-demo` | Assigned case queue |
| `owner-qa` | `saathi-owner-qa` | Automated checks |

These deliberately public demo credentials belong to the loopback sandbox. A first owner sign-in can claim the guest quote in that browser if the account has no existing case.

## Walkthrough

1. Keep the five-person office profile or enter a description and review the advisor's suggestions before requesting a quote. The catalogue computes ₹2,619 per month before tax. Backup connectivity raises it to ₹3,118 per month and adds ₹1,500 setup.
2. Accept the quote, sign in as the owner, choose K11 and explicitly consent. Use the supplied address specimen. At the exercise date of 15 July 2026, the 31 October 2025 bill is 257 days old.
3. Request partner assistance. In another browser session, sign in as the partner, open the assigned case, and acknowledge it.
4. Choose the team-created recent address proof for the same fictional applicant. Process the supplied signatory specimen and optionally the GST and Udyam specimens. A failed optional check can be cleared for products that do not require it.
5. Place the simulated order. The tracker shows payment, installation, product states and activation. Submitting the same accepted quote again returns the same order.

## Verification and data handling

With Python and `httpx` installed, run `python -X utf8 tests/smoke.py` while the stack runs. This uses the dedicated owner-qa account. Later runs on the same data recheck the completed order. Use `--skip-model` if the weights are still downloading, or `--quote-only` to isolate sign-in and pricing.

The document flow accepts supplied synthetic specimens and a separately labelled team-created replacement. It encrypts a transient object before OCR and deletes it after processing. Only decisions, dates and masked references remain in the case. The local advisor processes only business descriptions. Use fictional inputs throughout.

The prototype simulates serviceability, payment, installation, activation, and registry lookup. Approved rates, tax, customer identity, security, retention, and operator integrations are prerequisites for a real pilot. The full implementation plan is in the parent workspace under `output/Signal_Airtel_SYNC_Implementation_Plan.md`.

