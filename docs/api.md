# API guide

Interactive reference (generated from the code): `http://127.0.0.1:8765/api/docs`.
All errors are `application/problem+json` with a stable `code`, a human `title` and, where useful, a `hint`.

## Importing a video

### 1. Declare the file
```http
POST /api/v1/projects/{project_id}/assets
{"filename": "Episode 12.mp4", "size_bytes": 734003200,
 "rights_type": "own_content", "rights_note": null, "rights_confirmed": true}
```
- `rights_type`: `own_content` | `licensed` | `permission_from_owner` | `public_domain_or_cc` | `other`
  (`other` requires `rights_note`). `rights_confirmed` must be `true`. The declaration is stored with the video.
- `filename` is for display only (path parts are stripped). Allowed extensions: mp4, mov, m4v, mkv, webm, avi, ts,
  mts, m2ts, flv — the real check is done on the content.
- Refused up front: `413 UPLOAD_TOO_LARGE` (over `CLIPPER_MAX_UPLOAD_BYTES`), `507 DISK_FULL`
  (needs ~1.3 × file size + 1 GB free).

Response `201` includes `id`, `status: "uploading"` and `upload_url`.

### 2. Upload in chunks (resumable)
```http
PUT /api/v1/assets/{id}/upload
Upload-Offset: 0
<first ≤ 64 MiB of the file>
```
Repeat with `Upload-Offset` = bytes already accepted (`received_bytes` in the response).
- Interrupted? `GET /api/v1/assets/{id}/upload` → `{"offset": n}` and continue from `n`.
- Wrong offset → `409 OFFSET_MISMATCH` with `"offset": n`.
- A failed or oversized chunk is discarded entirely; the offset stays at the last good chunk.
- One upload per asset at a time (`409 UPLOAD_IN_PROGRESS`). Uploads idle longer than
  `CLIPPER_TEMP_RETENTION_HOURS` are discarded (`UPLOAD_ABANDONED`).

When the last byte arrives the status becomes `processing` and an import job starts (`import_job_id`).

### 3. Wait for `ready`
Poll `GET /api/v1/assets/{id}` (or `GET /api/v1/jobs/{import_job_id}` for `progress` / `progress_message`).

| Final status | Meaning |
|---|---|
| `ready` | Validated and prepared. `duration_ms`, `width`/`height` (as displayed, rotation applied), `fps`, `has_audio`, `warnings`, `media_info` are set |
| `failed` | `error_code`, `error_message`, `error_hint` explain why. The uploaded bytes are deleted |

Error codes: `MEDIA_UNREADABLE`, `MEDIA_UNSUPPORTED_CONTAINER`, `MEDIA_NO_VIDEO`, `MEDIA_UNSUPPORTED_CODEC`,
`MEDIA_TOO_LONG`, `MEDIA_TOO_SHORT`, `MEDIA_RESOLUTION_UNSUPPORTED`, `MEDIA_TRUNCATED`, `MEDIA_CORRUPT`,
`MEDIA_PROCESSING_FAILED`, `IMPORT_CANCELLED`, `UPLOAD_ABANDONED`.

Warnings (video still usable): `NO_AUDIO`, `VARIABLE_FRAME_RATE`, `HDR_COLORS_MAY_LOOK_WASHED_OUT`
(HDR is converted to SDR without tone-mapping in this version), `INTERLACED` (deinterlaced in the preview),
`HIGH_FRAME_RATE`.

### 4. Use the prepared media
| Endpoint | Content |
|---|---|
| `GET /api/v1/assets/{id}/proxy` | Preview MP4, short side ≤ 540 px, ≤ 30 fps, keyframe every second. Supports `Range` |
| `GET /api/v1/assets/{id}/thumbnails` | JPEG sprite; layout in `media_info.thumbs` (`count`, `interval_s`, `cols`, `rows`, `tile_w`, `tile_h`) |
| `GET /api/v1/assets/{id}/waveform` | `{"rate": 20, "peaks": [0..255, …]}` — one peak per 50 ms; empty if no audio |

### Deleting
`DELETE /api/v1/assets/{id}` removes the database row and **all** files (source, proxy, audio, thumbnails).
`409 ASSET_BUSY` while an import is running (it is cancelled; retry shortly).
`DELETE /api/v1/projects/{id}` removes the project, its videos and every file under it.
