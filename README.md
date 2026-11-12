# Garmin AI Coach

Ask questions about your own Garmin data in plain language. The app reads your
data through the
[Garmin Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/),
filters it, and sends only what a question needs to an AI model you choose.

![The app answering a sleep question with a chart](docs/screenshot.png)

It is a personal tool for one person. It is not affiliated with Garmin. There are
three ways to use it: on your computer, on your own free Cloudflare Worker, or as
a sample-data demo in the browser.

## Run it on your computer (your real data)

You need Python 3.9 or newer. There is nothing else to install.

```
git clone https://github.com/aihtn2708/garmin-ai-coach
cd garmin-ai-coach
python run.py
```

A browser tab opens at `http://127.0.0.1:8765`. Keys stay in the Python program's
memory and never reach the page. Press Ctrl+C in the terminal, or click Quit, to
stop.

## Run it on Cloudflare (your real data, from any device)

A free Cloudflare Worker serves the page and relays Garmin requests from the same
address, which is what gets past Garmin's refusal to answer web pages. The Worker
runs in **your own** Cloudflare account.

1. Create a free account at <https://dash.cloudflare.com> (no card needed).
2. Go to **Workers & Pages**, choose **Create**, then import this GitHub repository.
   Keep the defaults (deploy command `npx wrangler deploy`) and deploy.
3. Open the address Cloudflare gives you, `https://garmin-ai-coach.<your-name>.workers.dev`.
4. Recommended: in the Worker's settings, under **Domains & Routes**, turn on
   **Cloudflare Access** for that address and allow only your own email. Otherwise
   anyone who learns the address can use your relay with their own Garmin key.

Or from a terminal: `npx wrangler login`, then `npx wrangler deploy`.

What to know before you choose this over the local app:

- Your Garmin key and your Garmin data pass through the Worker on their way to and
  from Garmin. The Worker stores nothing and logs nothing
  ([`cloudflare/worker.js`](cloudflare/worker.js) is about 100 lines; read it), but
  they do transit Cloudflare's network in your account.
- Keys are held in the browser tab's memory, not in a program on your computer.
  If you tick "Remember keys on this device" they are also kept, encrypted, in
  that browser.
- The free plan allows 100,000 Worker requests a day. A question uses a handful.
  Scripts, styles and images do not count.
- Not yet tried against the real Garmin service. If Garmin turns away requests that
  come from Cloudflare, the local app remains the fallback.

## Try the demo in your browser (sample data)

**<https://aihtn2708.github.io/garmin-ai-coach/>**

The GitHub Pages copy is the same interface running on built-in sample data. It has
no relay, so it cannot load your own Garmin data: Garmin's API does not accept
requests from web pages (it sends no CORS headers; confirmed on 12 November 2026).
The page never asks for a Garmin key and its security policy does not allow it to
contact Garmin at all.

The AI part of the demo is real. Add a model key and the model analyses the sample
data. That key goes straight from the tab to the provider and is forgotten when
you close or reload the tab.

## Using it

1. **Connect Garmin** (local app or your Cloudflare Worker). Create a key at
   <https://connect.garmin.com/app/settings/personalApiToken> (pick the shortest
   expiry you are comfortable with) and paste it in. Or click "Try it with sample
   data" to look around first.
2. **Choose an AI model.** Pick a provider, paste its API key, click "Load models".
3. **Decide what the AI may see**, then ask a question.

The **data log** button in the top bar opens a panel listing every request that
left your computer, with the exact content sent to the AI model. It stays hidden
until you want it.

## Which model to use

Prices and limits as read on 12 November 2026. They change often; the links are the
source of truth.

| Provider | Cost | What happens to your prompts |
|---|---|---|
| **Google Gemini** | Free tier on Flash and Flash-Lite models ([pricing](https://ai.google.dev/gemini-api/docs/pricing)). Request limits are per project and shown in AI Studio. | On the free tier Google may use prompts to improve its products and human reviewers may read them. Enabling billing on the project stops that ([terms](https://ai.google.dev/gemini-api/terms)). |
| **OpenRouter** | Free models: 20 requests a minute; 50 a day, or 1,000 a day after $10 of credit purchases ([limits](https://openrouter.ai/docs/api-reference/limits)). | Depends on the company hosting each model. Several free models are on hosts that may train on prompts. The app asks OpenRouter to refuse those hosts by default ([policy](https://openrouter.ai/docs/guides/privacy/provider-logging)). |
| **Anthropic Claude** | Paid. Claude Haiku 5.5: $0.10 per million input tokens, $0.50 per million output ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)). | Paid API under Anthropic's commercial terms. |
| **DeepSeek** | Paid. `deepseek-flash`: $0.15 input / $0.60 output per million tokens off-peak, double at peak ([pricing](https://api-docs.deepseek.com/quick_start/pricing)). | Processed in China under DeepSeek's privacy policy. Read it before sending health data. |
| **Local model (Ollama)** | Free; uses your own hardware. Needs a model that supports tools. | Nothing leaves your computer. |

A typical question uses roughly 5,000 to 15,000 input tokens and a few hundred
output tokens (the app shows the real numbers under each answer). On Claude Haiku
5.5 or `deepseek-flash` that works out to well under one US cent per question.

If "no data used for training" matters more to you than "free", the options are a
local model, a paid API, or Gemini with billing enabled.

On Cloudflare and in the demo, the page calls the AI provider directly, so the
provider must accept requests from web pages. Gemini, OpenRouter and Anthropic do.
Reports about DeepSeek conflict. Ollama works if you start it with
`OLLAMA_ORIGINS` set to the page's address. The local app has no such limits.

## How it works

```
 Browser tab
   │
   ├── Garmin client ──────────► personal.connectapi.garmin.com   read-only GETs
   │        │
   │        ▼
   │   privacy filter: remove IDs, names, GPS, device details;
   │   alias activity IDs; summarise long series; cap size
   │        │
   ├── agent loop ◄────────────  the model may only call 6 fixed tools
   │        │
   └── provider adapter ───────► the one AI provider you picked
            │
            ▼
     log: every outbound request, shown in the page
```

In the local app these steps run in a small Python server on `127.0.0.1`
(`coach/`), and the page only draws the interface. On Cloudflare and in the demo
the same steps run inside the page (`docs/core.js`). On Cloudflare the Garmin
client goes through the Worker's relay; in the demo it is replaced by sample data.

The model never receives a URL, a key or raw Garmin output. It can ask for one of
six things (a metric over a date range, one day's detail, the activity list, one
activity, your profile and zones, or a chart). The app validates the request,
fetches from Garmin, filters the result and passes it on.

| File | Job |
|---|---|
| `docs/index.html`, `app.js`, `style.css`, `fonts/` | The interface, shared by all versions. No libraries; the one typeface (Barlow Condensed, SIL Open Font License) is served from the same folder |
| `docs/core.js` | Cloudflare and demo: Garmin client (via relay), sample data, privacy filter, tools, providers, agent, log, encrypted key vault |
| `cloudflare/worker.js`, `wrangler.jsonc` | Cloudflare Worker: serves the page, relays read-only Garmin requests |
| `run.py` | Starts the local app |
| `coach/server.py` | Local web server and its protections |
| `coach/net.py` | The only Python code that opens network connections; host allowlist |
| `coach/garmin.py` | Read-only Garmin client, token handling |
| `coach/privacy.py` | What is removed or shrunk before a model sees anything |
| `coach/tools.py` | The six tools and their input checks |
| `coach/providers.py` | Gemini, OpenRouter, DeepSeek, Ollama (OpenAI format) and Anthropic |
| `coach/agent.py` | The question-answering loop and the system prompt |
| `coach/ledger.py`, `keystore.py`, `demo.py` | Outbound log, optional OS keychain, sample data |

`docs/core.js` is a port of the Python modules, and a test checks that both privacy
filters give the same output on the same input.

Security and governance details are in [SECURITY.md](SECURITY.md).

## What has and has not been tested

```
python -m unittest discover -s tests -t . -v     # local app, 19 tests
node --test tests/test_core.js                   # page engine, key vault and Cloudflare relay, 15 tests
```

Tested:

- the privacy filter, tool input checks, range chunking and size limits, in both
  versions, plus a check that the JavaScript filter matches the Python one;
- the Garmin client against a local stand-in for Garmin's API (token exchange,
  token refresh, caching, path allowlist);
- both model wire formats against local stand-in servers, including Gemini's
  thought-signature round trip;
- the local server's guards and a full question-to-answer session;
- the Cloudflare Worker in Cloudflare's own runtime on a test machine: the page,
  its security headers, and the relay refusing every request that is off its
  list or not from its own page;
- remembering keys: nothing is stored without opting in, stored keys are never
  readable as plain text, a wrong passphrase or altered data fails to open, and
  "Forget everything" erases them;
- all three versions in Chrome, including that the pages store nothing by
  default, that the demo never sends a Garmin key, and that the browser blocks
  connections to unlisted hosts.

Not tested: anything with a real Garmin key or a real provider key, and the Worker
on Cloudflare's real network. None were available while building this, so expect
rough edges on first contact. Likely places:

- **Garmin and Cloudflare.** Whether Garmin accepts requests arriving from a
  Cloudflare Worker is unknown until you try it.

- **Garmin response shapes.** Garmin's Swagger page could not be read while
  building, so the filter does not assume field names; it works on any JSON. If
  an answer looks thin, open "See exactly what was sent" in the log to see what
  the model received.
- **Provider quirks.** Free OpenRouter models vary in how well they use tools. If
  one misbehaves, try another.

## Limits to know about

- The Personal API is pull-only and read-only. Some Garmin-computed metrics
  (VO2 max, recovery time and similar) are reported to be missing from it.
- Garmin publishes no rate limits and may throttle. The app caches responses for
  15 minutes and caps each question at 24 Garmin requests.
- Ranges are capped at 92 days per request (365 for daily summaries). For longer
  spans the model is told to ask for weekly or monthly averages.
- Activity FIT files (second-by-second data) are not downloaded or parsed.
- Garmin's terms say the key is for you alone, with tools you run for your own
  use. Never share your key. Deploy the Worker only in your own Cloudflare
  account, keep it to yourself, and never use someone else's deployment.
- This is not medical advice.

## Remembering your keys

By default you paste keys each time, and they are forgotten when you leave.

**On Cloudflare and in the demo:** tick **Remember keys on this device** at the
bottom of the setup panel. Your keys, chosen model and settings are encrypted and
kept in that browser, and Garmin reconnects by itself next time. Add a passphrase
if anyone else uses the same browser; you will be asked for it on each visit.
Untick the box, or click "Forget everything", to erase them.

**Local app:** install the optional keychain helper, restart, and tick the box.
Keys go into your operating system's keychain.

```
pip install keyring
```

## Updating the hosted demo

Cloudflare redeploys by itself when you push to `main`, if you imported the
repository there. GitHub Pages serves the `gh-pages` branch, which is a copy of the `docs/` folder on
`main`. After changing anything in `docs/`, commit to `main`, then republish:

```
git push origin main
git push origin `git subtree split --prefix docs main`:gh-pages --force
```

To drop the second branch, set Settings → Pages to branch `main`, folder `/docs`,
and delete `gh-pages`.

## Ideas for later

- Parse FIT files locally for pace, power and heart-rate-zone analysis
- A weekly summary generated on a schedule
- Token-by-token streaming of answers
- A cost estimate per answer from live provider prices

## Licence

MIT. See [LICENSE](LICENSE).
