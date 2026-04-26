import os
import sys
import pickle
import numpy as np
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import sequence

import tensorflow.keras.preprocessing.text
sys.modules['keras.preprocessing.text'] = tensorflow.keras.preprocessing.text

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

class PhishingPredictor:
    def __init__(self, model_path="D:\\Workspace\\DEPHIDES\\DLT\\dephides\\test_results\\cnn_bilstm\\best_model.h5", tokenizer_path="D:\\Workspace\\DEPHIDES\\DLT\\dephides\\src\\tokenizer.pkl"):
        self.sequence_length = 200
        self.categories = ['phishing', 'legitimate']
        
        if not os.path.exists(model_path) or not os.path.exists(tokenizer_path):
            print("Error: Could not find model or tokenizer files.")
            print("Make sure best_model.h5 and tokenizer.pkl are in the same folder as this script.")
            sys.exit(1)

        print("Loading Tokenizer...")
        with open(tokenizer_path, 'rb') as f:
            self.tokenizer = pickle.load(f)
            
        print("Loading Model...")
        self.model = load_model(model_path)
        print("System Ready!\n" + "-"*40)

    def predict(self, url):
        url_lower = url.lower()
        seq = self.tokenizer.texts_to_sequences([url_lower])
        padded_seq = sequence.pad_sequences(seq, maxlen=self.sequence_length)
        
        probabilities = self.model.predict(padded_seq, verbose=0)[0]
        prediction_index = np.argmax(probabilities)
        
        result = self.categories[prediction_index]
        confidence = probabilities[prediction_index] * 100
        
        if result == 'phishing':
            print(f"🚨 [PHISHING]  Confidence: {confidence:.2f}% -> {url}")
        else:
            print(f"✅ [LEGITIMATE] Confidence: {confidence:.2f}% -> {url}")

if __name__ == "__main__":
    predictor = PhishingPredictor()
    
    print("\nDeep Learning Phishing Detection System")
    print("Type 'exit' to quit.\n")
    
    while True:
        user_url = input("Enter a URL to scan: ")
        if user_url.lower() == 'exit':
            break
        if user_url.strip() == "":
            continue
            
        predictor.predict(user_url)