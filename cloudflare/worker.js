/* Garmin AI Coach on Cloudflare Workers (free plan).

   Two jobs, nothing else:
     1. Serve the page in docs/ and mark it as running behind this relay.
     2. Forward read-only requests under /garmin/ to Garmin's Personal API.
        Garmin does not answer web pages directly (no CORS headers); this
        Worker has the same address as the page, so the browser allows it.

   What the relay will NOT do: store anything, log anything, forward to any
   host but Garmin, accept any path or method outside the fixed list, or
   answer a request that did not come from its own page.

   Your Garmin key and data pass through this Worker in transit. It runs in
   your own Cloudflare account; deploy it only there. */

const GARMIN = "https://personal.connectapi.garmin.com";
const TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token";
const D = "\\d{4}-\\d{2}-\\d{2}";
const ALLOWED_GET = [
  `^/usersummary-service/stats/(stress|steps|hydration)/daily/${D}/${D}$`,
  "^/usersummary-service/v2/dailysummaries$",
  `^/goal-service/goal/user/effective/weightgoal/${D}/${D}$`,
  `^/weight-service/weight/range/${D}/${D}$`,
  `^/nutrition-service/food/logs/${D}$`,
  "^/userprofile-service/userprofile/settings$",
  "^/sleep-service/sleep/(dailySleep|dailySleepSummariesByDate)$",
  "^/wellness-service/wellness/bodyBattery/reports/daily$",
  "^/wellness-service/wellness/dailyHeartRate$",
  `^/wellness-service/wellness/daily/spo2acclimation/${D}$`,
  `^/hrv-service/hrv/${D}$`,
  `^/hrv-service/hrv/daily/${D}/${D}$`,
  "^/biometric-service/heartRateZones/v2$",
  `^/periodichealth-service/menstrualcycle/(summary|dayview)/${D}$`,
  `^/fitnessage-service/fitnessage/v2/${D}$`,
  `^/wellnessactivity-service/activity/summary/range/${D}/${D}$`,
  "^/activitylist-service/activities/search/activities$",
  "^/activity-service/activity/activityTypes/simple$",
  "^/activity-service/activity/\\d{1,20}(/workouts)?$",
].map((p) => new RegExp(p));

const PAGE_HEADERS = {
  "Content-Security-Policy":
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; " +
    "connect-src 'self' https://generativelanguage.googleapis.com https://openrouter.ai " +
    "https://api.anthropic.com https://api.deepseek.com http://127.0.0.1:11434 http://localhost:11434; " +
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cache-Control": "no-store",
};

function refuse(status, message) {
  return new Response(JSON.stringify({ relay_error: message }), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}

function upstreamBase(env) {
  // Tests may point the relay at a stand-in on this machine. Nothing else is accepted.
  const override = env && env.TEST_GARMIN_UPSTREAM;
  if (override) {
    try {
      const host = new URL(override).hostname;
      if (host === "127.0.0.1" || host === "localhost") return override.replace(/\/$/, "");
    } catch (e) { /* ignore a malformed value */ }
  }
  return GARMIN;
}

export async function relay(request, url, env) {
  // Only this Worker's own page may use the relay. Browsers set Sec-Fetch-Site
  // themselves (page script cannot forge it), and the custom header cannot be
  // sent by another site without a CORS preflight, which is never granted.
  if (request.headers.get("Sec-Fetch-Site") !== "same-origin" || request.headers.get("X-Coach-Relay") !== "1") {
    return refuse(403, "Only this app's own page may use the relay.");
  }
  const path = url.pathname.slice("/garmin".length);
  const auth = request.headers.get("Authorization") || "";
  let scheme;
  if (request.method === "POST" && path === TOKEN_PATH && !url.search) scheme = "ApiKey";
  else if (request.method === "GET" && ALLOWED_GET.some((p) => p.test(path)) && url.search.length <= 400) scheme = "Bearer";
  else return refuse(404, "This path or method is not on the relay's read-only list.");
  if (!new RegExp(`^${scheme} [\\x21-\\x7e]{8,4096}$`).test(auth)) return refuse(401, "Missing or malformed credentials.");

  let upstream;
  try {
    upstream = await fetch(upstreamBase(env) + path + url.search, {
      method: request.method,
      headers: { Authorization: auth, Accept: scheme === "ApiKey" ? "*/*" : "application/json" },
      redirect: "manual",
    });
  } catch (e) {
    return refuse(502, "The relay could not reach Garmin.");
  }
  if (upstream.status >= 300 && upstream.status < 400) return refuse(502, "Garmin answered with a redirect, which the relay does not follow.");
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") || "application/octet-stream",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/garmin" || url.pathname.startsWith("/garmin/")) return relay(request, url, env);
    if (request.method !== "GET" && request.method !== "HEAD") return refuse(405, "Method not allowed.");

    const asset = await env.ASSETS.fetch(request);
    if (!(asset.headers.get("Content-Type") || "").includes("text/html")) return asset;
    // Tell the page it sits behind the relay, and send the security policy as real headers.
    const page = new HTMLRewriter()
      .on("html", { element(el) { el.setAttribute("data-mode", "relay"); } })
      .transform(asset);
    const headers = new Headers(page.headers);
    for (const [k, v] of Object.entries(PAGE_HEADERS)) headers.set(k, v);
    return new Response(page.body, { status: page.status, headers });
  },
};
