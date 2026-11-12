# Security and data governance

The design goal: your Garmin data and your keys stay under your control, and you
can see everything that leaves your computer.

There are two versions with the same interface and the same filtering. They differ
in where the keys sit.

| | Browser version (GitHub Pages) | Local version (`python run.py`) |
|---|---|---|
| Where the logic runs | In the page (`docs/core.js`) | In a Python program on `127.0.0.1` |
| Where keys are held | The tab's JavaScript memory | The Python program's memory; the page never holds them |
| Who you trust for the code | Whoever can push to the GitHub repo, on every page load | The copy you downloaded |
| Garmin from a web page (CORS) | Must be allowed by Garmin; unconfirmed | Not affected |
| Keys between sessions | Never stored | Optional OS keychain |

If you want the stronger of the two, use the local version.

## Where data lives

| Thing | Browser version | Local version | How long |
|---|---|---|---|
| Garmin API key and 24 h token | Tab memory | Program memory | Until you close or reload the tab / quit, or click "Forget everything" |
| AI provider keys | Tab memory | Program memory | Same |
| Garmin data fetched for a question | Tab memory (15-minute cache) | Program memory | Same |
| Conversation and outbound log | Tab memory | Tab and program memory | Same; you can export them |
| Disk, cookies, localStorage, sessionStorage | Nothing | Nothing, unless you opt in to the OS keychain | |

There is no database, no cloud storage, no account, no analytics and no telemetry.
GitHub serves the page's files and so sees that your IP address loaded them, as any
web host does. It never receives your keys, your Garmin data or your questions.

## What leaves your computer

Only two kinds of request, both listed live in the "What left this computer" panel:

1. **To Garmin** (`personal.connectapi.garmin.com`): your key, to get a token; then
   read-only requests for your own data.
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
| The page loads or contacts something unexpected | Content-Security-Policy: scripts and styles from the page's own origin only; connections only to Garmin, the listed AI providers and Ollama on this computer. The browser enforces it |
| A model reply smuggles data out through a link or image | Replies are inserted as text; links are not clickable and images are never loaded |
| Text in your data (an activity name, say) tries to instruct the model | The model can only call six read-only tools with validated arguments; it cannot fetch URLs, send messages or change anything. The system prompt tells it to treat tool results as data |
| The model asks for more than it should | Category switches enforced in code; date, range and size limits; 24 Garmin requests and 8 model steps per question |
| The app is tricked into calling another host | One function makes all connections and refuses any host not on the allowlist (Garmin plus the selected provider); HTTPS is required; redirects are refused; no cookies are sent |
| Something writes to your Garmin account | Only GET requests to a fixed list of read-only paths are possible |
| Keys end up in logs | Keys travel in request headers, which are never recorded |
| Keys linger | Memory only; "Forget everything" clears keys, data, conversation and log |

## Extra controls in the local version

| Risk | Control |
|---|---|
| Another device on the network reaches the app | Server binds to `127.0.0.1` only |
| A malicious web page calls the local API (CSRF, DNS rebinding) | Every API call needs a random per-launch token sent as a header; `Host` and `Origin` are checked; POSTs must be JSON |
| The token leaks through browser history or logs | It travels in the URL fragment, which is never sent to a server, and is removed from the address bar on load |
| Page JavaScript is tampered with | Keys never reach the page; a stricter policy (`connect-src 'self'`) stops the page contacting anything but the local server |

## Extra risks in the browser version

- **The repo is the trust anchor.** The page runs whatever is in the repo's `docs/`
  folder at the moment you load it. Anyone who can push to the repo can change
  that code. Protect the GitHub account with two-factor authentication, and do
  not add collaborators you would not hand your keys to. To pin a version you
  have read, use the local version from a specific commit.
- **Keys are in the page.** Anything that can run script in the tab (a malicious
  browser extension, for example) could read them. The local version keeps keys
  out of the page.
- **Anthropic's browser header.** Calling Claude from a page requires the header
  `anthropic-dangerous-direct-browser-access`. The name warns developers not to
  ship their own key to visitors. Here the key is the visitor's own and stays in
  their tab.

## Your side of the bargain

- Give the Garmin key a short expiry (30 or 90 days) and delete it in Garmin
  Connect when you stop using the app. Deleting the key also cancels its tokens.
- Use a separate AI provider key for this app and set a spending limit on it
  where the provider offers one.
- Do not expose the local version's port to a network or use either version on a
  shared machine or shared browser profile.
- Keep the key to yourself. Garmin's Personal API terms limit it to you and to
  tools you run for your own use.
- Browser extensions can read pages you open, including this one. Use a browser
  profile you trust.

## Known gaps

- Field-name filtering can miss an identifier with an unexpected name.
- Neither Python nor JavaScript can reliably erase secrets from memory; "forgotten" means dereferenced
  and gone when the process exits.
- Not tested against live Garmin or provider endpoints, and Garmin's support for
  browser requests is unconfirmed (see the README).
- No independent security review has been done.

## Reporting a problem

Open an issue at <https://github.com/aihtn2708/garmin-ai-coach/issues>. Do not paste keys or
personal data into an issue.
