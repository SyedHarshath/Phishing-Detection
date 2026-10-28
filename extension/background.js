const API_BASE = "https://optimus-eewm.onrender.com";
// Bump when collection scope, destinations, storage, or use changes.
const CONSENT_VERSION = "2026-10-20-nav-url-scope-v2";
const pendingScans = new Map();
const EXCLUDED_DOMAINS = [
  "google.com", "google.co.in", "bing.com", "yahoo.com",
  "duckduckgo.com", "ecosia.org", "search.brave.com",
  "yandex.com", "baidu.com"
];

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "loading") return;

  const url = tab.url;
  if (!isScannableUrl(url) || isExcludedDomain(url)) return;

  hasConsent().then(consented => consented ? scanUrl(url) : null).then(result => {
    if (!result) return;
    chrome.tabs.sendMessage(tabId, {
      action: "showResult",
      data: result
    }).catch(() => {});
  }).catch(error => {
    console.error("OPTIMUS prediction error:", error);
  });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "predict") {
    hasConsent().then(consented => {
      if (!consented) throw new Error("Open OPTIMUS and accept the data notice before scanning.");
      return scanUrl(message.url);
    }).then(data =>
      sendResponse({success: true, data})
    ).catch(error =>
      sendResponse({success: false, error: error.message})
    );
    return true;
  }

  if (message.action === "feedback") {
    hasConsent().then(consented => {
      if (!consented) throw new Error("Enable OPTIMUS scanning in the popup before sending feedback.");
      return sendFeedback(message.predictionId, message.actualLabel);
    }).then(data =>
      sendResponse({success: true, data})
    ).catch(error =>
      sendResponse({success: false, error: error.message})
    );
    return true;
  }
});

async function scanUrl(url) {
  if (pendingScans.has(url)) return pendingScans.get(url);
  const pending = performScan(url);
  pendingScans.set(url, pending);
  try {
    return await pending;
  } finally {
    if (pendingScans.get(url) === pending) pendingScans.delete(url);
  }
}

async function performScan(url) {
  const response = await fetchWithTimeout(`${API_BASE}/predict`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({url})
  });

  if (!response.ok) {
    let detail = `API request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return await response.json();
}

async function sendFeedback(predictionId, actualLabel) {
  const response = await fetchWithTimeout(`${API_BASE}/feedback`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      prediction_id: predictionId,
      actual_label: actualLabel
    })
  });

  if (!response.ok) {
    let detail = `Feedback request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return await response.json();
}

async function fetchWithTimeout(url, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } catch (error) {
    if (error.name === "AbortError") throw new Error("OPTIMUS request timed out.");
    throw new Error("OPTIMUS service could not be reached.");
  } finally {
    clearTimeout(timer);
  }
}

async function hasConsent() {
  const stored = await chrome.storage.local.get("optimusConsentVersion");
  return stored.optimusConsentVersion === CONSENT_VERSION;
}

function isScannableUrl(url) {
  if (!url) return false;
  try {
    const p = new URL(url);
    return p.protocol === "http:" || p.protocol === "https:";
  } catch (_) {
    return false;
  }
}

function isExcludedDomain(url) {
  try {
    const hostname = new URL(url).hostname.toLowerCase();
    return EXCLUDED_DOMAINS.some(d =>
      hostname === d || hostname.endsWith("." + d)
    );
  } catch (_) {
    return true;
  }
}
