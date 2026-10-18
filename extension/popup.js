const urlInput = document.getElementById("url");
const scanButton = document.getElementById("scanButton");
const status = document.getElementById("status");
const result = document.getElementById("result");
const resultBadge = document.getElementById("resultBadge");
const confidence = document.getElementById("confidence");
const modelVersion = document.getElementById("modelVersion");
const apiStatus = document.getElementById("apiStatus");

document.addEventListener("DOMContentLoaded", initialize);
scanButton.addEventListener("click", scanUrl);

async function initialize() {
  try {
    const tabs = await chrome.tabs.query({
      active: true,
      currentWindow: true
    });

    if (!tabs.length || !tabs[0].url) {
      showStatus("Could not read the current tab URL.");
      scanButton.disabled = true;
      return;
    }

    urlInput.value = tabs[0].url;

    if (!isScannableUrl(tabs[0].url)) {
      showStatus("This browser page cannot be scanned. Open a normal website.");
      scanButton.disabled = true;
    }
  } catch (error) {
    showStatus(`Unable to read current tab: ${error.message}`);
    scanButton.disabled = true;
  }

  checkApi();
}

async function scanUrl() {
  const url = urlInput.value.trim();

  if (!url) {
    showStatus("Please enter a URL.");
    return;
  }

  if (!isScannableUrl(url)) {
    showStatus("Please enter a normal HTTP or HTTPS URL.");
    return;
  }

  hideStatus();
  hideResult();

  scanButton.disabled = true;
  scanButton.textContent = "Scanning...";

  chrome.runtime.sendMessage(
    {
      action: "predict",
      url: url
    },
    (response) => {
      scanButton.disabled = false;
      scanButton.textContent = "Scan URL";

      if (chrome.runtime.lastError) {
        showStatus(`Extension error: ${chrome.runtime.lastError.message}`);
        return;
      }

      if (!response || !response.success) {
        showStatus(response?.error || "Could not connect to the OPTIMUS API.");
        return;
      }

      displayResult(response.data);
    }
  );
}

async function checkApi() {
  try {
    const response = await fetch("http://127.0.0.1:8000/health");

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();

    apiStatus.textContent = data.model_loaded
      ? "API connected - Model loaded"
      : "API connected - Model not loaded";
  } catch (_) {
    apiStatus.textContent = "API offline - Start FastAPI on port 8000";
  }
}

function displayResult(data) {
  const prediction = String(data.prediction || "").toLowerCase();

  resultBadge.textContent =
    prediction === "phishing" ? "WARNING: PHISHING" : "LEGITIMATE";

  resultBadge.className = "result-badge";

  if (prediction === "phishing" || prediction === "legitimate") {
    resultBadge.classList.add(prediction);
  }

  confidence.textContent = `${data.confidence}%`;
  modelVersion.textContent = data.model_version || "-";

  result.classList.remove("hidden");
}

function showStatus(message) {
  status.textContent = message;
  status.classList.remove("hidden");
}

function hideStatus() {
  status.classList.add("hidden");
}

function hideResult() {
  result.classList.add("hidden");
}

function isScannableUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (_) {
    return false;
  }
}
