---
module: migration_import
last_verified_commit: 0eb12fe
---

# migration_import — technical overview

## Goal

Import a [DPMF v0.1](https://github.com/dentaltix/dental-bridge/blob/main/spec/dpmf-v0.1.md)
file produced by `dental-bridge` into the current Dentia clinic.
The DPMF carries 32 canonical entity types (patients, appointments,
budgets, payments, fiscal documents, etc.) plus a `_files` manifest
pointing at the binaries (radiographs / PDFs) that travel out of band.

## Pipeline

```
upload (.dpm) → validate (decrypt → decompress → hash → version)
              → preview (counts + samples + warnings + files summary)
              → execute (BackgroundTask: topological mapper run)
              → completed
                ↑
                └── (in parallel) sync agent uploads binaries via
                                   POST /jobs/{id}/binaries
```

State machine: `uploaded → validating → validated → previewing →
executing → completed | failed`.

## Tables

| Table                              | Purpose                                                                 |
|------------------------------------|-------------------------------------------------------------------------|
| `migration_import_jobs`            | One row per upload. Status, progress, _meta snapshot, integrity hashes. |
| `migration_import_entity_mappings` | Idempotency keystone: `(clinic_id, source_system, entity_type, canonical_uuid)` → Dentia row. |
| `migration_import_file_stagings`   | `_files` manifest staging; one row per declared binary.                 |
| `migration_import_warnings`        | DPMF `_warnings` + warnings raised by the importer.                     |
| `migration_import_raw_entities`    | Forward-compat catch-all for entity types without a dedicated mapper.   |

All five tables cascade on `clinic_id` and on `job_id`. Uninstall via
Alembic drops them cleanly without touching any other module's data.

## DPMF reader

- **Magic-byte detection** (`SQLi` / `28 b5 2f fd` / `DPME`) at the
  edge — file is never opened blindly.
- **AES-256-GCM + scrypt** (N=2¹⁷, r=8, p=1) for the `DPME` envelope.
  Implementation mirrors `dental_bridge.core.dpmf.crypto` byte-for-byte.
- **zstd** for the compressed layer; stream-decompressed into a temp
  file so multi-GB payloads stay out of memory.
- **Integrity hash** reproduces the canonical line format from
  `dental_bridge.core.dpmf.writer.DpmfWriter._compute_logical_hash`
  exactly. Mismatch → job → `failed`.
- **Format-version gate**: rejects `format_version >= 1.0`. Anything
  in the `0.x` series is accepted.
- Temporary plaintext (post-decrypt / post-decompress) lives inside a
  single `tempfile.TemporaryDirectory` and is wiped on context exit
  including exception paths.

## Mappers

Walk entity types in dependency order (`dpmf/iter.ENTITY_ORDER` — 7
levels per `dental-bridge/docs/migration_order.md`). Each mapper:

1. Consults `MappingResolver.get()` — bails if already mapped.
2. Resolves cross-entity references via the resolver (never trusts
   raw canonical UUIDs as Dentia FKs).
3. Calls the target module's service (e.g. `PatientService.create_patient`).
4. Records the mapping via `MappingResolver.set()`.

Per-entity mapper failures emit a warning and continue; one bad row
doesn't fail the whole job.

### Legal hashes from the source system

Source files may carry legal hash fields stamped by whatever certified
invoicing system the clinic used before (Veri\*Factu chains, ATCUD, QR
payloads). Dentia ships no tax-authority integration, so the
`fiscal_document` mapper:

1. Always creates the commercial invoice (number, totals, dates).
2. Preserves the legal fields (`legal_hash`, `hash_control`, `atcud`,
   `qr_code`) **verbatim** — never re-signed, never validated — and only
   when `ImportJob.import_fiscal_compliance` is true.
3. Emits a `fiscal_document.legal_fields_dropped` warning when the file
   *had* legal data the operator chose not to preserve.

Note that `billing.Invoice` has no columns for these fields today, so
step 2 is a no-op against the current schema (`_stamp_legal_fields`
guards with `hasattr`). The opt-in is kept as the seam for a future
compliance module that adds them.

## Binary ingestion

The sync agent POSTs each binary to `POST /jobs/{id}/binaries` with
the sha256 in a form field. The receiver:

1. Recomputes sha256 — refuses if the client lied.
2. Looks up `FileStaging` by `(job_id, sha256)`.
3. Resolves the owning patient via `entity_mappings`
   (`patient_document` rows map to `patients.id` directly).
4. Hands off to `media.DocumentService.create_document` — clinic-
   scoped path, thumbnails, MIME validation, all reused.
5. Updates the staging row to `received` + stores the document id.

`404` is the agent's "retry later" signal (most often: the parent
patient_document mapping hasn't been written yet because execute is
still mid-pipeline).

## File staging on disk

Uploaded `.dpm` files live under
`{STORAGE_LOCAL_PATH}/migration-import/<clinic_id>/<job_id>.dpm` by
default — i.e. a sibling of `media`'s storage root, which docker-compose
mounts as the `storage_data` volume owned by `appuser`. Override via
`MIGRATION_IMPORT_STAGING_DIR` when DPMF binaries should live on a
separate disk. Wiped on module uninstall.

## Not in scope today

- Sync-agent CLI (ships in dental-bridge).
- DPMF v0.2+ entities (DICOM-aware manifest, streaming append,
  decoded odontogram).
- Duplicate detection against pre-existing clinic data.
- Rollback / undo.
