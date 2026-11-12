# Security and data governance

The design goal: your Garmin data and your keys stay under your control, and you
can see everything that leaves your computer.

There are two versions with the same interface and the same filtering.

| | Local app (`python run.py`) | Hosted demo (GitHub Pages) |
|---|---|---|
| Garmin data | Your own, through your Personal API key | Built-in sample data only |
| Why | | Garmin's API refuses requests from web pages (no CORS headers). The only way round is a relay server, which would see your key. This project does not use one |
| Where the logic runs | A Python program on `127.0.0.1` | In the page (`docs/core.js`) |
| Garmin key | In the Python program's memory; the page never holds it | Never asked for; the page's policy forbids contacting Garmin |
| AI provider key | In the Python program's memory | In the tab's memory |
| Keys between sessions | Optional OS keychain | Never stored |

## Where data lives

| Thing | Local app | Hosted demo | How long |
|---|---|---|---|
| Garmin API key and 24 h token | Program memory | Not used | Until you quit or click "Forget everything" |
| AI provider keys | Program memory | Tab memory | Until you quit / close or reload the tab, or click "Forget everything" |
| Garmin data fetched for a question | Program memory (15-minute cache) | Sample data only | Same |
| Conversation and outbound log | Tab and program memory | Tab memory | Same; you can export them |
| Disk, cookies, localStorage, sessionStorage | Nothing, unless you opt in to the OS keychain | Nothing | |

There is no database, no cloud storage, no account, no analytics and no telemetry.
GitHub serves the demo page's files and so sees that your IP address loaded them, as
any web host does. It never receives your keys or your questions.

## What leaves your computer

Only two kinds of request, both listed live in the "What left this computer" panel:

1. **To Garmin** (`personal.connectapi.garmin.com`), local app only: your key, to get
   a token; then read-only requests for your own data.
2. **To the AI provider you chose**: the system prompt, your questions, and the
   filtered data needed to answer them. Click "See exactly what was sent" on any
   entry to read the full request body.

If you choose the local model option, the second kind never leaves your computer.

**The honest limit:** whatever is sent to an AI provider is then held under that
provider's terms, not yours. The app shows a summary of each provider's terms
beside the provider choice and marks the ones that may use prompts for training.
To keep health data out of training sets, use a local model, a paid API, or
Gemini with billing enabled.

## What is removed before a model sees anything

Applied to every Garmin response (`coach/privacy.py`):

- **Always removed:** user, owner, profile and device identifiers; any field ending
  in `Id`, `PK`, `UUID`; display name, full name, email, phone, profile images and
  URLs; latitude, longitude, polylines and other coordinates; time zone.
- **Replaced:** activity IDs become session-local aliases (A1, A2 ...). Birth date
  becomes age in years.
- **Strict mode also removes:** activity names, location names, descriptions,
  notes and other free text.
- **Shrunk:** long series (for example a day of heart-rate samples) become
  min/max/mean plus a sample; record lists become compact tables; each result is
  capped in size.
- **Never requested unless you allow it:** each data category has a switch. Weight,
  nutrition and menstrual-cycle data are off by default. Tools for a category
  that is off are not even described to the model.

The rules match on field names, because Garmin's schema can change. That means an
unusually named field could slip through. The request inspector exists so you can
check.

## Controls in both versions

| Risk | Control |
|---|---|
| A compromised dependency steals keys | There are no dependencies: hand-written JavaScript and the Python standard library. No CDN, no web fonts |
| The page loads or contacts something unexpected | Content-Security-Policy: scripts and styles from the page's own origin only. The local page may connect only to the local server; the hosted demo only to the listed AI providers and Ollama on this computer. The browser enforces it |
| A model reply smuggles data out through a link or image | Replies are inserted as text; links are not clickable and images are never loaded |
| Text in your data (an activity name, say) tries to instruct the model | The model can only call six read-only tools with validated arguments; it cannot fetch URLs, send messages or change anything. The system prompt tells it to treat tool results as data |
| The model asks for more than it should | Category switches enforced in code; date, range and size limits; 24 Garmin requests and 8 model steps per question |
| The app is tricked into calling another host | One function makes all connections and refuses any host not on the allowlist (Garmin plus the selected provider); HTTPS is required; redirects are refused; no cookies are sent |
| Something writes to your Garmin account | Only GET requests to a fixed list of read-only paths are possible |
| Keys end up in logs | Keys travel in request headers, which are never recorded |
| Keys linger | Memory only; "Forget everything" clears keys, data, conversation and log |

## Extra controls in the local app

| Risk | Control |
|---|---|
| Another device on the network reaches the app | Server binds to `127.0.0.1` only |
| A malicious web page calls the local API (CSRF, DNS rebinding) | Every API call needs a random per-launch token sent as a header; `Host` and `Origin` are checked; POSTs must be JSON |
| The token leaks through browser history or logs | It travels in the URL fragment, which is never sent to a server, and is removed from the address bar on load |
| Page JavaScript is tampered with | Keys never reach the page; a stricter policy (`connect-src 'self'`) stops the page contacting anything but the local server |

## Things to know about the hosted demo

- **It handles no Garmin key and no real health data.** The only secret it ever
  holds is an AI provider key, if you add one.
- **The repo is the trust anchor.** The page runs whatever is in the repo's `docs/`
  folder at the moment you load it. Protect the GitHub account with two-factor
  authentication.
- **The AI key is in the page.** Anything that can run script in the tab (a
  malicious browser extension, for example) could read it. Use a key with a
  spending limit, or use the local app.
- **Anthropic's browser header.** Calling Claude from a page requires the header
  `anthropic-dangerous-direct-browser-access`. The name warns developers not to
  ship their own key to visitors. Here the key is the visitor's own and stays in
  their tab.

## Your side of the bargain

- Give the Garmin key a short expiry (30 or 90 days) and delete it in Garmin
  Connect when you stop using the app. Deleting the key also cancels its tokens.
- Use a separate AI provider key for this app and set a spending limit on it
  where the provider offers one.
- Do not expose the local app's port to a network or use either version on a
  shared machine or shared browser profile.
- Keep the key to yourself. Garmin's Personal API terms limit it to you and to
  tools you run for your own use.
- Browser extensions can read pages you open, including this one. Use a browser
  profile you trust.

## Known gaps

- Field-name filtering can miss an identifier with an unexpected name.
- Neither Python nor JavaScript can reliably erase secrets from memory; "forgotten" means dereferenced
  and gone when the process exits.
- The local app has not yet been tested against live Garmin or provider endpoints
  (see the README).
- No independent security review has been done.

## Reporting a problem

Open an issue at <https://github.com/aihtn2708/garmin-ai-coach/issues>. Do not paste keys or
personal data into an issue.
