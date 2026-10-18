from ml.prediction import PhishingPredictor


def main():
    predictor = PhishingPredictor()

    print("\nDeep Learning Phishing Detection System")
    print("Type 'exit' to quit.\n")

    while True:
        user_url = input("Enter a URL to scan: ")
        if user_url.lower() == "exit":
            break
        if user_url.strip() == "":
            continue

        result = predictor.predict(user_url)
        label = result["prediction"].upper()
        confidence = result["confidence"]
        print(f"[{label}] Confidence: {confidence:.2f}% -> {user_url}")


if __name__ == "__main__":
    main()
