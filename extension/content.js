let currentPredictionId = null;
let notificationTimer = null;

chrome.runtime.onMessage.addListener((message) => {
  if (message.action === "showResult") {
    displayResult(message.data);
  }
});

function displayResult(data) {
  currentPredictionId = data.prediction_id || null;
  const prediction = String(data.prediction || "").toLowerCase();
  removeExistingUI();

  if (prediction === "phishing") showPhishingWarning(data);
  else if (prediction === "legitimate") showLegitimateNotification(data);
}

function showPhishingWarning(data) {
  const notification = document.createElement("div");
  notification.id = "OPTIMUS-warning";
  notification.setAttribute("role", "alert");
  notification.setAttribute("aria-live", "assertive");
  pinTopRight(notification, 420);

  const box = document.createElement("div");
  box.id = "OPTIMUS-box";

  const icon = document.createElement("div");
  icon.className = "pg-icon";
  icon.textContent = "!";

  const heading = document.createElement("h1");
  heading.textContent = "Phishing Website Detected";

  const message = document.createElement("p");
  message.textContent =
    "OPTIMUS's AI model has detected that this website may be a phishing website.";

  const confidence = document.createElement("div");
  confidence.className = "pg-confidence";
  confidence.append(createTextElement("span", "Confidence"));
  confidence.append(createTextElement("strong", `${data.confidence}%`));

  const model = document.createElement("div");
  model.className = "pg-model";
  model.textContent = `Model: ${data.model_version || "Unknown"}`;

  const buttons = document.createElement("div");
  buttons.className = "pg-buttons";

  const backButton = createButton("pg-back", "Go Back");
  backButton.onclick = () => window.history.back();

  const stayButton = createButton("pg-stay", "Stay on Website");
  stayButton.onclick = () => {
    notification.remove();
    style.remove();
  };

  buttons.append(backButton, stayButton);

  const feedback = document.createElement("div");
  feedback.className = "pg-feedback";
  feedback.append(document.createTextNode("Was this prediction wrong?"));

  const legitimateButton = createButton("pg-legitimate", "Actually Legitimate");
  legitimateButton.onclick = () => submitFeedback("legitimate");
  const phishingButton = createButton("pg-phishing", "Report as Phishing");
  phishingButton.onclick = () => submitFeedback("phishing");
  feedback.append(phishingButton, legitimateButton);

  const feedbackStatus = document.createElement("div");
  feedbackStatus.id = "pg-feedback-status";

  box.append(icon, heading, message, confidence, model, buttons, feedback, feedbackStatus);
  notification.append(box);

  const style = createStyle();
  document.documentElement.append(style, notification);
}

function showLegitimateNotification(data) {
  const notification = document.createElement("div");
  notification.id = "OPTIMUS-legitimate";
  pinTopRight(notification, 360);

  const body = document.createElement("div");
  body.append(createTextElement("strong", "OPTIMUS"));
  body.append(createTextElement("span", "Website appears legitimate"));
  body.append(createTextElement("span", `${data.confidence}% confidence`));
  body.append(createTextElement("small", "Model estimate only; this is not a safety guarantee."));

  const phishingButton = createButton("pg-phishing", "Report as Phishing");
  phishingButton.onclick = () => submitFeedback("phishing");
  const legitimateButton = createButton("pg-legitimate", "Report as Legitimate");
  legitimateButton.onclick = () => submitFeedback("legitimate");

  const status = document.createElement("small");
  status.id = "pg-legit-status";

  body.append(phishingButton, legitimateButton, status);
  notification.append(body);

  const style = createStyle();
  document.documentElement.append(style, notification);

  notificationTimer = setTimeout(() => {
    notification.remove();
    style.remove();
    notificationTimer = null;
  }, 5000);
}

function submitFeedback(actualLabel) {
  if (!currentPredictionId) return;
  const buttons = document.querySelectorAll("#pg-legitimate, #pg-phishing");
  if ([...buttons].some(button => button.disabled)) return;
  buttons.forEach(button => button.disabled = true);

  chrome.runtime.sendMessage({
    action: "feedback",
    predictionId: currentPredictionId,
    actualLabel
  }, (response) => {
    const status = document.getElementById("pg-feedback-status") ||
                   document.getElementById("pg-legit-status");

    if (chrome.runtime.lastError) {
      if (status) status.textContent = "Feedback error.";
      buttons.forEach(button => button.disabled = false);
      return;
    }

    if (!response || !response.success) {
      if (status) status.textContent = response?.error || "Could not save feedback.";
      buttons.forEach(button => button.disabled = false);
      return;
    }

    if (status) status.textContent = "Feedback recorded";
    // Keep controls disabled after success to prevent repeat submission.
  });
}

function removeExistingUI() {
  if (notificationTimer) {
    clearTimeout(notificationTimer);
    notificationTimer = null;
  }
  document.querySelectorAll(
    "#OPTIMUS-warning, #optimus-warning, #OPTIMUS-legitimate, #OPTIMUS-style"
  ).forEach(element => element.remove());
}

function pinTopRight(element, width) {
  element.style.setProperty("position", "fixed", "important");
  element.style.setProperty("inset", "auto", "important");
  element.style.setProperty("top", "20px", "important");
  element.style.setProperty("right", "20px", "important");
  element.style.setProperty("bottom", "auto", "important");
  element.style.setProperty("left", "auto", "important");
  element.style.setProperty("z-index", "2147483647", "important");
  element.style.setProperty("width", `min(${width}px, calc(100vw - 32px))`, "important");
  element.style.setProperty("box-sizing", "border-box", "important");
}

function createTextElement(tagName, text) {
  const element = document.createElement(tagName);
  element.textContent = text;
  return element;
}

function createButton(id, text) {
  const button = document.createElement("button");
  button.id = id;
  button.textContent = text;
  return button;
}

function createStyle() {
  const style = document.createElement("style");
  style.id = "OPTIMUS-style";
  style.textContent = `
    #OPTIMUS-warning{position:fixed!important;inset:auto!important;top:20px!important;right:20px!important;bottom:auto!important;left:auto!important;width:min(420px,calc(100vw - 32px))!important;z-index:2147483647!important;font-family:Arial,sans-serif;background:#fff;border:1px solid #fecaca;border-left:5px solid #b91c1c;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.24);color:#222}
    #OPTIMUS-box{padding:16px;text-align:left}
    .pg-icon{display:inline-grid;place-items:center;width:25px;height:25px;border-radius:50%;background:#fee2e2;color:#b91c1c;font-size:17px;font-weight:700}
    #OPTIMUS-box h1{margin:8px 0;font-size:18px;color:#b91c1c}#OPTIMUS-box p{margin:6px 0;color:#444;font-size:13px;line-height:1.4}
    .pg-confidence{margin-top:12px;padding:10px;background:#fff1f2;border-radius:8px;display:flex;justify-content:space-between;gap:10px}.pg-confidence strong{color:#b91c1c}
    .pg-model{margin-top:8px;font-size:12px;color:#666}.pg-buttons{display:flex;gap:8px;margin-top:12px}.pg-buttons button{flex:1;padding:9px 8px;border:0;border-radius:7px;cursor:pointer;font-weight:600}
    #pg-back{background:#b91c1c;color:#fff}#pg-stay{background:#e5e7eb;color:#333}.pg-feedback{margin-top:12px;padding-top:10px;border-top:1px solid #eee;color:#666;font-size:12px}.pg-feedback button,#OPTIMUS-legitimate button{margin:6px 5px 0 0;padding:7px 9px;border:1px solid #d1d5db;border-radius:7px;background:#fff;cursor:pointer}
    #pg-feedback-status,#pg-legit-status{display:block;margin-top:8px;color:#15803d;font-size:12px}
    #OPTIMUS-legitimate{position:fixed!important;inset:auto!important;top:20px!important;right:20px!important;bottom:auto!important;left:auto!important;width:min(360px,calc(100vw - 32px))!important;box-sizing:border-box;z-index:2147483647!important;font-family:Arial,sans-serif;background:#fff;border-radius:10px;box-shadow:0 5px 25px rgba(0,0,0,.2);padding:14px 18px;font-size:13px}
    #OPTIMUS-legitimate div{display:flex;flex-direction:column;gap:4px}#OPTIMUS-legitimate strong{color:#15803d;font-size:15px}#OPTIMUS-legitimate span{color:#555}
  `;
  return style;
}
