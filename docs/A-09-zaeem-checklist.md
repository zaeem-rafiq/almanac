# A-09 — the things only Zaeem can do

Everything else in A-09 is built, tested and committed. These need credentials, so they are yours.

Run all of these from `/Users/zaeemkhan/Documents/almanac`.

> **This file was rewritten on 2026-09-07.** Its first version was written before the extraction
> engine moved to Gemini on Vertex AI (`038dbe6`) and told you to top up the Anthropic balance, set
> `ANTHROPIC_API_KEY`, and set `LLM_MODEL=claude-opus-5` on the host. All three are wrong now — the
> last actively so, since it would send a Claude model id to Vertex. **Do none of them.**

---

## 1. Log in to Vercel · ~1 min

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel login
```

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel whoami
```

## 2. Create a Vertex AI service-account key · ~3 min · the one genuinely new step

The host has no `gcloud`, so there is no Application Default Credentials file for it to find.
`google.auth.default()` checks exactly four places — the `GOOGLE_APPLICATION_CREDENTIALS` file, the
gcloud well-known file, App Engine, and the GCE metadata server — and a Vercel function and a
GitHub runner have none of them. A key has to be handed in explicitly.

In the Google Cloud console for **`polygraph-hackathon`**: create a service account, grant it
**Vertex AI User** (`roles/aiplatform.user`), then create a JSON key and download it.

Nothing else on `trainmatch-494604` is touched — the project is named explicitly everywhere.

Then, from wherever the key downloaded:

```bash
base64 -i ~/Downloads/polygraph-hackathon-*.json | gh secret set GOOGLE_SERVICE_ACCOUNT_JSON
```

```bash
base64 -i ~/Downloads/polygraph-hackathon-*.json | vercel env add GOOGLE_SERVICE_ACCOUNT_JSON production
```

`almanac/gcp_credentials.py` decodes it, writes it to a `0600` file, and points
`GOOGLE_APPLICATION_CREDENTIALS` at it — because google-auth reads a *path* and has no env var for
inline JSON. It accepts raw JSON too, so an un-base64'd paste also works.

**Delete the downloaded key file afterwards.** It is a private key.

## 3. The other two secrets · ~2 min

```bash
gh secret set FMP_API_KEY
```

```bash
base64 -i token.json | gh secret set YT_TOKEN_JSON
```

`YT_TOKEN_JSON` is optional: without it the nightly job falls back to `--source corpus` by design.

## 4. Deploy, then tell keepalive where to look

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel deploy --prod
```

```bash
gh variable set ALMANAC_URL --body "https://<the-url-vercel-printed>"
```

---

## What is already handled

| | |
|---|---|
| `evals/` reaches the host | `.vercelignore` no longer excludes it; `vercel.json` `includeFiles` lists it. Excluding it made `web/app.py` raise `FileNotFoundError` at import and 500 **every** route. |
| the app survives without it anyway | `_load_measured` degrades instead of crashing (`94c13d7`), and the page says so rather than printing `undefined`. |
| `google-genai` is installed on the host | It was missing from `requirements.txt`, which pinned `anthropic==1.4.0` instead — a clean install then `ImportError`ed inside `/api/lint`. |
| `client_secret.json` cannot be uploaded | Added to `.vercelignore`. A `.vercelignore` makes the CLI ignore `.gitignore`, so gitignored secrets must be re-listed. |
| keepalive does not go red on a healthy host | It curled `/api/report` and demanded 200, but that returns a documented 503 until a nightly run commits a scan. It now warms `/` and reports `/api/report` without gating on it. |

## Known gap

`reports/latest.json` is a gitignored build artifact, so a fresh deploy has no scan to serve and
`/api/report` returns 503 until the first nightly run commits one. Trigger it by hand after
deploying if you want the report populated before judging:

```bash
gh workflow run nightly
```
