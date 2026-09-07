# A-09 — the four things only Zaeem can do

Everything else in A-09 is built and committed. These four need credentials, so they are yours.
Rough order of value: **1 and 2 unblock three of the four proofs.** 3 is the deploy itself.

Run all of these from `/Users/zaeemkhan/Documents/almanac`.

---

## 0. ⛔️ TOP THE ANTHROPIC ACCOUNT UP  ·  blocks the most  ·  DO THIS FIRST

Found by running the nightly sequence for real, not by reading config:

```
anthropic.BadRequestError: Error code: 400 — 'Your credit balance is too low to access the
Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'
request_id: req_011CepuLRSb1FezG93TyhBKF
```

**This is wider than A-09.** Every path that calls the model is down: `almanac scan`,
`/api/lint` on the web page, and A-05's eval harness. Deploying and setting secrets will not
help until it is cleared — the nightly job would go green on install and then fail at
`almanac/extract.py:427`, and a live `/api/lint` would return a 400.

<https://console.anthropic.com/settings/billing>

`FMP_API_KEY` is fine — `rates --refresh` ran clean and wrote all four keys.

---

## 1. Log in to Vercel  ·  ~1 min  ·  unblocks proofs 1 and 3

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel login
```

Then confirm it took:

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel whoami
```

## 2. Set the three GitHub Actions secrets  ·  ~2 min  ·  unblocks proof 2

`ANTHROPIC_API_KEY` and `FMP_API_KEY` — paste each value when prompted (nothing is echoed):

```bash
gh secret set ANTHROPIC_API_KEY
```

```bash
gh secret set FMP_API_KEY
```

`YT_TOKEN_JSON` is base64 of the local `token.json`, piped straight in so it never lands in your
shell history:

```bash
base64 -i token.json | gh secret set YT_TOKEN_JSON
```

**The nightly workflow runs without `YT_TOKEN_JSON`** — it falls back to `--source corpus`. Set it
only when you want the YouTube path. Note the quota: it is exhausted until midnight Pacific, so a
YouTube-source run before the reset will fail on 403 regardless of the secret.

## 3. Deploy  ·  ~3 min  ·  produces the URL everything else needs

⚠️ **Do this only after A-08 is merged to `main`.** `web/app.py` on `main` is still a 57-byte
placeholder — the real app is on `claude/stoic-babbage-5e3238` with no PR open yet. Deploying
before it lands gives you a host that 500s on every route.

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel link --yes
```

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel deploy --prod
```

Then set the two host env vars. `ALMANAC_WRITE` stays **unset** and there is deliberately **no
`FMP_API_KEY`** on the host — the app reads `facts/rates.json`:

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel env add ANTHROPIC_API_KEY production
```

```bash
printf 'claude-opus-5' | vercel env add LLM_MODEL production
```

Redeploy so the env vars take effect:

```bash
export PATH="$HOME/Library/pnpm:$PATH" && vercel deploy --prod
```

## 4. Tell the keepalive workflow where to curl  ·  ~30 s

Paste the deployment URL from step 3 (no trailing slash, e.g. `https://almanac-xxxx.vercel.app`):

```bash
gh variable set ALMANAC_URL
```

This is a repo **variable**, not a secret — the keepalive job fails loudly with a clear message if
it is unset, rather than curling nothing and reporting green.

---

## Then hand it back

Reply with the deployment URL and I will run the four proofs and close them out. Or run them
yourself:

```bash
gh workflow run nightly.yml && sleep 45 && gh run list --workflow=nightly.yml --limit 1
```

## Fallback hosts, if Vercel refuses

`flyctl` and `render` are **not installed** on this machine — a fallback needs an install first:

```bash
brew install flyctl
```

