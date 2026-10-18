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
  const overlay = document.createElement("div");
  overlay.id = "optimus-warning";

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
    overlay.remove();
    style.remove();
  };

  buttons.append(backButton, stayButton);

  const feedback = document.createElement("div");
  feedback.className = "pg-feedback";
  feedback.append(document.createTextNode("Was this prediction wrong?"));

  const legitimateButton = createButton("pg-legitimate", "Actually Legitimate");
  legitimateButton.onclick = () => submitFeedback("legitimate");
  feedback.append(legitimateButton);

  const feedbackStatus = document.createElement("div");
  feedbackStatus.id = "pg-feedback-status";

  box.append(icon, heading, message, confidence, model, buttons, feedback, feedbackStatus);
  overlay.append(box);

  const style = createStyle();
  document.documentElement.append(style, overlay);
}

function showLegitimateNotification(data) {
  const notification = document.createElement("div");
  notification.id = "OPTIMUS-legitimate";

  const body = document.createElement("div");
  body.append(createTextElement("strong", "OPTIMUS"));
  body.append(createTextElement("span", "Website appears legitimate"));
  body.append(createTextElement("span", `${data.confidence}% confidence`));

  const phishingButton = createButton("pg-phishing", "Report as Phishing");
  phishingButton.onclick = () => submitFeedback("phishing");

  const status = document.createElement("small");
  status.id = "pg-legit-status";

  body.append(phishingButton, status);
  notification.append(body);

  const style = createStyle();
  document.documentElement.append(style, notification);

  notificationTimer = setTimeout(() => {
    notification.remove();
    style.remove();
  }, 5000);
}

function submitFeedback(actualLabel) {
  if (!currentPredictionId) return;

  chrome.runtime.sendMessage({
    action: "feedback",
    predictionId: currentPredictionId,
    actualLabel
  }, (response) => {
    const status = document.getElementById("pg-feedback-status") ||
                   document.getElementById("pg-legit-status");

    if (chrome.runtime.lastError) {
      if (status) status.textContent = "Feedback error.";
      return;
    }

    if (!response || !response.success) {
      if (status) status.textContent = response?.error || "Could not save feedback.";
      return;
    }

    if (status) status.textContent = "Feedback recorded";
    document.querySelectorAll("#pg-legitimate, #pg-phishing").forEach((button) => {
      button.disabled = true;
    });
  });
}

function removeExistingUI() {
  if (notificationTimer) clearTimeout(notificationTimer);
  document.getElementById("OPTIMUS-warning")?.remove();
  document.getElementById("OPTIMUS-legitimate")?.remove();
  document.getElementById("OPTIMUS-style")?.remove();
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
    #OPTIMUS-warning{position:fixed;inset:0;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;z-index:2147483647;font-family:Arial,sans-serif}
    #OPTIMUS-box{width:460px;max-width:calc(100vw - 40px);padding:30px;background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.4);text-align:center}
    .pg-icon{font-size:55px;font-weight:700;color:#b91c1c}.pg-confidence{margin-top:20px;padding:15px;background:#fee2e2;border-radius:10px;display:flex;justify-content:space-between}.pg-confidence strong{color:#b91c1c}
    #OPTIMUS-box h1{color:#b91c1c}.pg-model{margin-top:12px;font-size:12px;color:#777}.pg-buttons{display:flex;gap:10px;margin-top:25px}.pg-buttons button{flex:1;padding:12px;border:0;border-radius:8px;cursor:pointer;font-weight:bold}
    #pg-back{background:#b91c1c;color:#fff}#pg-stay{background:#e5e7eb;color:#333}.pg-feedback{margin-top:18px;padding-top:14px;border-top:1px solid #eee;color:#666;font-size:12px}.pg-feedback button,#OPTIMUS-legitimate button{margin-top:8px;padding:8px 12px;border:1px solid #d1d5db;border-radius:7px;background:#fff;cursor:pointer}
    #pg-feedback-status,#pg-legit-status{display:block;margin-top:8px;color:#15803d;font-size:12px}
    #OPTIMUS-legitimate{position:fixed;top:20px;right:20px;z-index:2147483647;font-family:Arial,sans-serif;background:#fff;border-radius:10px;box-shadow:0 5px 25px rgba(0,0,0,.2);padding:14px 18px;font-size:13px}
    #OPTIMUS-legitimate div{display:flex;flex-direction:column;gap:4px}#OPTIMUS-legitimate strong{color:#15803d;font-size:15px}#OPTIMUS-legitimate span{color:#555}
  `;
  return style;
}
