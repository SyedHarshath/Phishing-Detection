from __future__ import annotations
import json
import sys
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"

def request(path, method="GET", payload=None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=body,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))

def main():
    try:
        status, health = request("/health")
        print("HEALTH:", status, health)
        if status != 200 or health.get("model_loaded") is not True:
            raise RuntimeError("Health check failed.")

        status, result = request("/predict", "POST", {"url": "https://example.com"})
        print("PREDICT:", status, result)
        if status != 200:
            raise RuntimeError("Prediction request failed.")

        print("Docker smoke test PASSED.")
    except urllib.error.URLError as exc:
        print(f"Could not reach Dockerized API: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"Smoke test FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
