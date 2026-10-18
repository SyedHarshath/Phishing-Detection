const API_BASE = "http://127.0.0.1:8000";
const EXCLUDED_DOMAINS = [
  "google.com", "google.co.in", "bing.com", "yahoo.com",
  "duckduckgo.com", "ecosia.org", "search.brave.com",
  "yandex.com", "baidu.com"
];

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "loading") return;

  const url = tab.url;
  if (!isScannableUrl(url) || isExcludedDomain(url)) return;

  scanUrl(url).then(result => {
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
    scanUrl(message.url).then(data =>
      sendResponse({success: true, data})
    ).catch(error =>
      sendResponse({success: false, error: error.message})
    );
    return true;
  }

  if (message.action === "feedback") {
    sendFeedback(message.predictionId, message.actualLabel).then(data =>
      sendResponse({success: true, data})
    ).catch(error =>
      sendResponse({success: false, error: error.message})
    );
    return true;
  }
});

async function scanUrl(url) {
  const response = await fetch(`${API_BASE}/predict`, {
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
  const response = await fetch(`${API_BASE}/feedback`, {
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
