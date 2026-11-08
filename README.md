# Udyam Saathi

Team Signal · Vellore Institute of Technology (VIT), Vellore · Airtel SYNC 2026

Udyam Saathi is a local sandbox for the fictional Accel Business case. The owner can describe business needs, confirm an eligible quote, verify synthetic documents, request partner help, and track a simulated order. The app uses React, TypeScript, i18next, FastAPI, Pydantic, PostgreSQL, Keycloak, private S3-compatible SeaweedFS storage, PaddleOCR, and a local Qwen model through Ollama.

[Watch the 2-minute 25-second walkthrough](docs/Udyam_Saathi_Demo.mp4) to see the catalogue quote, stale-document recovery, partner queue, and per-service activation tracker.

## Start the sandbox

Clone this repository and extract the organizer’s synthetic data pack separately. Run PowerShell from the repository root with Docker Desktop running. Point `SAATHI_DATA_PATH` at the folder containing `product_catalogue.csv`, `mock_gstin_registry.csv`, `mock_udyam_registry.csv`, and `kyc_documents/`.

```powershell
git clone https://github.com/Mayank2142/Udyam-Saathi.git
cd Udyam-Saathi
$env:SAATHI_DATA_PATH = 'C:\path\to\YTL_TrackA_Udyam_Digital_Data'
docker compose up --build -d --wait
docker compose exec -T model ollama pull qwen2.5:1.5b
```

Open [the app](http://127.0.0.1:8174/), [API docs](http://127.0.0.1:8174/docs), or [health status](http://127.0.0.1:8174/api/health). The first build downloads images and model weights. The first OCR check initializes its models. The guided form works during advisor setup.

The case data mounts read-only. The loader recognizes the exact leading-backtick typo on `P09` in the original pack, so use the CSV as supplied. Other malformed and duplicate IDs still fail validation. The app and sign-in ports are mapped to loopback. `docker compose stop` stops the app and keeps local cases.

| Synthetic user | Password | Purpose |
|---|---|---|
| `owner-try` | `saathi-owner-try` | Owner account for your walkthrough |
| `owner-demo` | `saathi-owner-demo` | Recorded walkthrough |
| `partner-demo` | `saathi-partner-demo` | Assigned case queue |
| `owner-qa` | `saathi-owner-qa` | Automated checks |

These deliberately public demo credentials belong to the loopback sandbox. A first owner sign-in can claim the guest quote in that browser if the account has no existing case.

## Walkthrough

1. Keep the five-person office profile or enter a description and review the advisor's suggestions before requesting a quote. If the advisor leaves site count or budget unresolved, enter an explicit value. The catalogue computes ₹2,619 per month before tax. Backup connectivity raises it to ₹3,118 per month and adds ₹1,500 setup.
2. Accept the quote, sign in as the owner, choose K11 and explicitly consent. Use the supplied address specimen. At the exercise date of 15 July 2026, the 31 October 2025 bill is 257 days old.
3. Request partner assistance. In another browser session, sign in as the partner, open the assigned case, and acknowledge it.
4. Choose the team-created recent address proof for the same fictional applicant. Process the supplied signatory specimen and optionally the GST and Udyam specimens. A failed optional check can be cleared for products that do not require it.
5. Place the simulated order. The tracker shows payment, installation, product states and activation. Submitting the same accepted quote again returns the same order.

## Verification and data handling

With Python and `httpx` installed, run `python -X utf8 tests/smoke.py` while the stack runs. Add `--require-fresh` for a first run against empty PostgreSQL storage. It checks quote revision and budget blocks, K11 correction, partner acknowledgement and two concurrent checkout requests. Later runs recheck the completed order on the dedicated `owner-qa` account. Use `--skip-model` while weights download, or `--quote-only` to isolate sign-in and pricing.

The document flow accepts supplied synthetic specimens and a separately labelled team-created replacement. It encrypts a transient object before OCR and deletes it after processing. Only decisions, dates and masked references remain in the case. The local advisor processes only business descriptions. Use fictional inputs throughout.

GST checks compare the printed legal name and trade name independently with the mock record. Unreadable fields require recapture. Conflicts return an action to correct the evidence or request partner help. An in-progress checkout with a GST pass recorded before this change requires a fresh check.

To rerun the 47 supplied specimens, including the K07 name-conflict regression, use the running sandbox:

```powershell
Get-Content -Raw tests/specimens.py | docker compose exec -T app python -
```

### Reproduce the recovery and resilience probes

From the repository root, use the original organizer data pack and the `SAATHI_DATA_PATH` setting above. Run against a fresh project. The test creates synthetic cases, temporarily holds outbox jobs, and injects one OCR timeout to check cleanup. The override uses internal relays and publishes no host ports.

```powershell
$probe = @('-p', 'saathi-recovery-qa', '-f', 'compose.yaml', '-f', 'tests/recovery-probe.compose.yaml')
docker compose @probe up --build -d --wait
docker compose @probe exec -T app python /probe-tests/run_recovery_probe.py
docker compose @probe down --volumes
```

The first OCR request may download the recognition models. The probe uses the bundled synthetic accounts and supplied K11 address and signatory specimens. It emits JSON for three runs from receipt of the stale-proof result to checkout readiness with the same accepted quote. The later checks cover a duplicate database job, a later-due job, held fibre with active email and security, consent withdrawal, the old partner link, and encrypted temporary-object cleanup. Timing is local automation and varies by host.

The prototype simulates serviceability, payment, installation, activation, and registry lookup. Approved rates, tax, customer identity, security, retention, and operator integrations are prerequisites for a real pilot.

## Airtel-inspired frontend and narrated demo

The frontend uses a responsive red-and-white product workspace, an animated connectivity illustration, persistent navigation, distinct quote and verification cards, visible focus states, and reduced-motion support.

Browser verification covers 360–1440px layouts, language switching, email-seat validation, unconfirmed coverage, over-budget blocking, failover repricing, saved-quote persistence, and the complete synthetic owner/partner/document/order journey. The mobile quote's decorative ring is contained within its card.

The video records an `owner-try` walkthrough. A fresh Compose project with empty data stores starts with no completed orders. Existing owner records in a running instance remain available after sign-in. A private browser session lets you start a guest quote.

The walkthrough uses the real local sandbox with synthetic inputs, synchronized captions, and locally synthesized male Indian English narration.
