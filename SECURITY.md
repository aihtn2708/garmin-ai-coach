# Security and data governance

The design goal: your Garmin data and your keys stay under your control, and you
can see everything that leaves your computer.

There are three versions with the same interface and the same filtering, in order
of how much you have to trust anything outside your own computer.

| | Local app (`python run.py`) | Your Cloudflare Worker | Demo (GitHub Pages) |
|---|---|---|---|
| Garmin data | Your own | Your own | Built-in sample data only |
| How Garmin is reached | Directly from a Python program on your computer | Through the Worker's relay, in your Cloudflare account | Never |
| Who sees your Garmin key and data in transit | You and Garmin | You, Garmin, and Cloudflare's network (the Worker forwards and keeps nothing) | Nobody; never asked for |
| Where keys are held | Python program's memory; the page never holds them | The browser tab's memory | AI key only, in the tab's memory |
| Keys between sessions | Not kept, unless you opt in to the OS keychain | Not kept, unless you opt in to encrypted storage in that browser | Same as Cloudflare, AI keys only |

Garmin's API refuses requests from web pages (it sends no CORS headers), so a page
needs something on a server to reach it. The local app is that something on your
own computer. The Worker is that something in your own Cloudflare account. This
project never uses a relay run by anyone else, and neither should you.

## Where data lives

| Thing | Local app | Cloudflare Worker version | Demo | How long |
|---|---|---|---|---|
| Garmin API key and 24 h token | Program memory | Tab memory; passes through the Worker per request | Not used | Until you quit / close or reload the tab, or click "Forget everything" |
| AI provider keys | Program memory | Tab memory; never sent to the Worker | Tab memory | Same |
| Garmin data fetched for a question | Program memory (15-minute cache) | Tab memory; passes through the Worker once | Sample data | Same |
| Conversation and outbound log | Tab and program memory | Tab memory | Tab memory | Same; you can export them |
| Browser storage or disk | Nothing, unless you opt in to the OS keychain | Nothing, unless you tick "Remember keys on this device" (then encrypted, see below) | Same as Cloudflare | Until you untick the box or click "Forget everything" |
| Cookies, Worker storage | Nothing | Nothing | Nothing | |

There is no database, no cloud storage, no account, no analytics and no telemetry.
GitHub serves the demo page's files and so sees that your IP address loaded them, as
any web host does. It never receives your keys or your questions.

## Remembering keys on this device

Off by default. When you tick the box in the Cloudflare version or the demo:

- **What is saved:** your Garmin key (Cloudflare version only), AI provider keys,
  the chosen model and the "what the AI may see" settings. Never your Garmin
  data, your questions or the answers.
- **Where:** in that browser's own storage for the page's address. It is not
  synced, not sent to the Worker, and not sent anywhere else.
- **How:** encrypted with AES-256-GCM through the browser's built-in Web Crypto.
- **With a passphrase:** the encryption key is derived from it (PBKDF2-SHA256,
  600,000 rounds) and is never stored. Without the passphrase the saved keys
  cannot be opened, by the page or by someone holding a copy of the browser's
  files. A weak passphrase can be guessed, so use a long one.
- **Without a passphrase:** the encryption key is a non-exportable key object in
  the same browser. That keeps the keys from being read as plain text out of
  storage or a backup, but anyone who can open the page in that browser profile
  can use them. Fine on a device only you use; add a passphrase otherwise.
- **To erase:** untick the box, click "Forget saved keys" on the unlock prompt, or
  click "Forget everything". Disconnecting Garmin removes the saved Garmin key.
  Clearing the site's data in the browser does the same.

What this does not protect against: code running inside the page while it is
unlocked (a malicious browser extension, or a changed copy of the app). The local
app keeps keys out of the page altogether, in memory or in the OS keychain.

## What leaves your computer

Only two kinds of request, both listed in the data log (the button in the top bar):

1. **To Garmin** (`personal.connectapi.garmin.com`): your key, to get a token; then
   read-only requests for your own data. From the local app these go direct. From
   the Cloudflare version they go to your Worker, which forwards them. The demo
   sends none.
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
| Keys linger | Memory only unless you opt in to remembering them; "Forget everything" clears keys (saved ones too), data, conversation and log |
| A font or other asset phones home | The typeface is served from the app's own folder; the policy allows fonts from the page's own address only |

## Extra controls in the local app

| Risk | Control |
|---|---|
| Another device on the network reaches the app | Server binds to `127.0.0.1` only |
| A malicious web page calls the local API (CSRF, DNS rebinding) | Every API call needs a random per-launch token sent as a header; `Host` and `Origin` are checked; POSTs must be JSON |
| The token leaks through browser history or logs | It travels in the URL fragment, which is never sent to a server, and is removed from the address bar on load |
| Page JavaScript is tampered with | Keys never reach the page; a stricter policy (`connect-src 'self'`) stops the page contacting anything but the local server |

## The Cloudflare Worker relay

What `cloudflare/worker.js` enforces, each covered by a test:

| Risk | Control |
|---|---|
| Another website uses your relay from a visitor's browser | Requests must carry the browser-set `Sec-Fetch-Site: same-origin` and a custom header; the relay sends no CORS headers, so other sites can neither send the custom header nor read a reply |
| The relay is used to reach some other server | The upstream host is fixed in code to Garmin's |
| The relay is used to change your Garmin account | Only GET on a fixed list of read-only paths, plus the one token exchange |
| Keys or data linger at Cloudflare | The Worker has no storage bindings, writes no logs (`observability` is off in `wrangler.jsonc`), and marks every reply `no-store` |
| Cookies or other headers leak upstream | Only `Authorization` and `Accept` are forwarded |
| The page is framed or loads foreign code | The Worker sends the Content-Security-Policy as a real header, including `frame-ancestors 'none'` |

What it cannot protect against:

- **Cloudflare itself.** Requests are decrypted inside Cloudflare's network to be
  forwarded. You are trusting Cloudflare the way any site behind it does.
- **Someone else using your address.** A person with a script (not a browser) who
  knows your Worker's address could relay their own Garmin key through it. They
  get nothing of yours, but it is your quota and your name on the relay. Turn on
  Cloudflare Access for the Worker's address so only you can open it.
- **Whoever can change the code.** If Cloudflare is connected to the GitHub repo,
  anyone who can push to `main` can change what the Worker and the page do.
  Protect both accounts with two-factor authentication.
- **The tab.** Keys are in the page's memory, where a malicious browser extension
  could read them. The local app keeps keys out of the page.

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
- Nothing has yet been tested against live Garmin or provider endpoints, and the
  Worker has only been run in Cloudflare's runtime on a test machine (see the README).
- No independent security review has been done.

## Reporting a problem

Open an issue at <https://github.com/aihtn2708/garmin-ai-coach/issues>. Do not paste keys or
personal data into an issue.
