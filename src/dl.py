import tensorflow as tf
import os
import json
import time
import argparse
import pickle
import datetime
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt

from tensorflow.keras.callbacks import Callback, EarlyStopping, ModelCheckpoint
from tensorflow.keras.utils import Sequence
from tensorflow.keras.preprocessing import sequence
from tensorflow.keras.preprocessing.text import Tokenizer
from tensorflow.keras.optimizers import Adam
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score
from sklearn import svm
from sklearn.naive_bayes import GaussianNB
from sklearn.ensemble import RandomForestClassifier

import sys
sys.path.append('/kaggle/input/datasets/balamsd07/phishing-project-files/Phishing_Project/src')
from dl_models import DlModels

plt.switch_backend('agg')

TEST_RESULTS = {
    'data': {}, 'embedding': {}, 'hiperparameter': {}, 'test_result': {}, 'epoch_times': []
}

gpus = tf.config.list_physical_devices('GPU')
if gpus:
    try:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
        print(f"Running on GPU: {len(gpus)} physical GPU(s) detected.")
    except RuntimeError as e:
        print(e)
else:
    print("Running on CPU. No GPU detected.")

class PhishingDataGenerator(Sequence):
    def __init__(self, file_path, tokenizer, batch_size, sequence_length, categories):
        self.file_path = file_path
        self.tokenizer = tokenizer
        self.batch_size = batch_size
        self.sequence_length = sequence_length
        self.categories = categories
        
        with open(file_path, 'r', encoding='utf-8') as f:
            self.data = [line.strip().split("\t") for line in f if len(line.strip().split("\t")) == 2]
                
    def __len__(self): return int(np.ceil(len(self.data) / float(self.batch_size)))

    def __getitem__(self, index):
        batch_data = self.data[index * self.batch_size:(index + 1) * self.batch_size]
        X = sequence.pad_sequences(self.tokenizer.texts_to_sequences([item[1] for item in batch_data]), maxlen=self.sequence_length)
        Y = np.zeros((len(batch_data), len(self.categories)))
        for i, item in enumerate(batch_data): Y[i, self.categories.index(item[0])] = 1.0
        return X, Y

class PhishingUrlDetection:
    def __init__(self):
        self.params = {
            'loss_function': 'categorical_crossentropy', 'optimizer': Adam(learning_rate=0.0003),
            'sequence_length': 200, 
            'batch_train': 512,  
            'batch_test': 512,
            'categories': ['phishing', 'legitimate'], 'epoch': 30, 'embedding_dimension': 128,
            
            'dataset_dir': "/kaggle/input/datasets/balamsd07/phishing-project-files/Phishing_Project/dataset/big_dataset",
            'tokenizer_path': "/kaggle/working/tokenizer.pkl" 
        }
        self.dl_models = DlModels(self.params['categories'], self.params['embedding_dimension'], self.params['sequence_length'])

    def set_params(self, args):
        self.params['epoch'] = int(args.epoch)
        self.params['architecture'] = args.architecture
        self.params['ml_model'] = args.ml_model
        save_name = self.params['ml_model'] if self.params['ml_model'] else self.params['architecture']
        
        self.params['result_dir'] = f"/kaggle/working/test_results/{save_name}/"
        os.makedirs(self.params['result_dir'], exist_ok=True)

    def build_tokenizer(self):
        if os.path.exists(self.params['tokenizer_path']):
            with open(self.params['tokenizer_path'], 'rb') as f: return pickle.load(f)
        urls = []
        for fn in ['train.txt', 'val.txt', 'test.txt']:
            with open(f"{self.params['dataset_dir']}/{fn}", 'r', encoding='utf-8') as f:
                urls.extend([line.strip().split("\t")[1] for line in f if len(line.strip().split("\t")) == 2])
        tokenizer = Tokenizer(lower=True, char_level=True, oov_token='-n-')
        tokenizer.fit_on_texts(urls)
        with open(self.params['tokenizer_path'], 'wb') as f: pickle.dump(tokenizer, f)
        return tokenizer

    def dl_algorithm(self):
        tokenizer = self.build_tokenizer()
        train_gen = PhishingDataGenerator(f"{self.params['dataset_dir']}/train.txt", tokenizer, self.params['batch_train'], self.params['sequence_length'], self.params['categories'])
        val_gen = PhishingDataGenerator(f"{self.params['dataset_dir']}/val.txt", tokenizer, self.params['batch_test'], self.params['sequence_length'], self.params['categories'])
        test_gen = PhishingDataGenerator(f"{self.params['dataset_dir']}/test.txt", tokenizer, self.params['batch_test'], self.params['sequence_length'], self.params['categories'])

        print(f"Building {self.params['architecture']} model on GPU...")
        model = getattr(self.dl_models, self.params['architecture'])(tokenizer.word_index)
        model.compile(loss=self.params['loss_function'], optimizer=self.params['optimizer'], metrics=['accuracy'])
            
        model.summary(print_fn=lambda x: TEST_RESULTS['hiperparameter'].update({'model_summary': TEST_RESULTS['hiperparameter'].get('model_summary', '') + x + '\n'}))

        checkpoint = ModelCheckpoint(filepath=f"{self.params['result_dir']}best_model.h5", monitor='val_loss', save_best_only=True, verbose=1)
        hist = model.fit(train_gen, epochs=self.params['epoch'], validation_data=val_gen, callbacks=[CustomCallBack(), EarlyStopping(monitor='val_loss', patience=8, restore_best_weights=True), checkpoint])

        t = time.time()
        score, acc = model.evaluate(test_gen)
        TEST_RESULTS['test_result'].update({'test_time': time.time() - t, 'test_acc': acc, 'test_loss': score})
        TEST_RESULTS['epoch_history'] = hist.history

        y_true = [self.params['categories'].index(item[0]) for item in test_gen.data]
        y_pred = np.argmax(model.predict(test_gen), axis=1)
        
        report = classification_report(y_true, y_pred, target_names=self.params['categories'])
        TEST_RESULTS['test_result'].update({'report': report, 'test_confusion_matrix': confusion_matrix(y_true, y_pred).tolist()})

        self.save_dl_results(model, tokenizer)

    def save_dl_results(self, model, tokenizer):
        TEST_RESULTS['date_time'] = str(datetime.datetime.now())
        TEST_RESULTS['embedding']['vocabulary_size'] = len(tokenizer.word_index)
        
        open(f"{self.params['result_dir']}raw_test_results.json", "w").write(json.dumps(TEST_RESULTS, indent=4))
        open(f"{self.params['result_dir']}classification_report.txt", "w").write(TEST_RESULTS['test_result']['report'])
        
        embeddings = model.layers[1].get_weights()[0]
        words_embeddings = {w: embeddings[idx].tolist() for w, idx in tokenizer.word_index.items() if idx < len(embeddings)}
        open(f"{self.params['result_dir']}char_embeddings.json", "w").write(json.dumps(words_embeddings))
        print(f"Academic logging complete. Files saved to {self.params['result_dir']}")

    def traditional_ml(self):
        tokenizer = self.build_tokenizer()
        print(f"Loading data subset for Traditional ML ({self.params['ml_model']})...")
        def get_subset(fn, size):
            with open(f"{self.params['dataset_dir']}/{fn}", 'r', encoding='utf-8') as f:
                data = [line.strip().split("\t") for i, line in enumerate(f) if i < size and len(line.strip().split("\t")) == 2]
            X = sequence.pad_sequences(tokenizer.texts_to_sequences([item[1] for item in data]), maxlen=self.params['sequence_length'])
            return X, np.array([self.params['categories'].index(item[0]) for item in data])

        x_train, y_train = get_subset('train.txt', 150000)
        x_test, y_test = get_subset('test.txt', 30000)

        t_start = time.time()
        if self.params['ml_model'] == "NB": model = GaussianNB().fit(x_train, y_train)
        elif self.params['ml_model'] == "RF": model = RandomForestClassifier(n_estimators=50, random_state=42, n_jobs=-1).fit(x_train, y_train)
        elif self.params['ml_model'] == "SVM": model = svm.LinearSVC(random_state=42, max_iter=1000).fit(x_train, y_train)
        
        y_pred = model.predict(x_test)
        acc = accuracy_score(y_test, y_pred)
        report = classification_report(y_test, y_pred, target_names=self.params['categories'])
        
        open(f"{self.params['result_dir']}classification_report.txt", "w").write(f"Model: {self.params['ml_model']}\nAccuracy: {acc:.4f}\n\n{report}")
        with open(f"{self.params['result_dir']}model.pkl", 'wb') as f: pickle.dump(model, f)
        print(f"{self.params['ml_model']} complete. Accuracy: {acc:.4f}.")

class CustomCallBack(Callback):
    def on_epoch_begin(self, batch, logs=None): self.epoch_time_start = time.time()
    def on_epoch_end(self, epoch, logs=None): TEST_RESULTS['epoch_times'].append(time.time() - self.epoch_time_start)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-ep", "--epoch", default=30)
    parser.add_argument("-arch", "--architecture", default="cnn_complex")
    parser.add_argument("-ml", "--ml_model", default=None)
    args = parser.parse_args()
    
    vc = PhishingUrlDetection()
    vc.set_params(args)
    vc.traditional_ml() if args.ml_model else vc.dl_algorithm()