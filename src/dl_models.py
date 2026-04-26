from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import (
    Input, Dense, Dropout, LSTM, Bidirectional, 
    Flatten, Conv1D, MaxPooling1D, Embedding, BatchNormalization
)

class DlModels:
    def __init__(self, categories, embed_dim, sequence_length):
        self.categories = categories
        self.embed_dim = embed_dim
        self.sequence_length = sequence_length
        self.output_nodes = len(categories)

    def rnn_base(self, char_index):
        model = Sequential()
        voc_size = len(char_index.keys())
        model.add(Input(shape=(self.sequence_length,)))
        model.add(Embedding(voc_size + 1, self.embed_dim))
        model.add(LSTM(128))
        model.add(Dense(self.output_nodes, activation='softmax'))
        return model

    def brnn_base(self, char_index):
        model = Sequential()
        voc_size = len(char_index.keys())
        model.add(Input(shape=(self.sequence_length,)))
        model.add(Embedding(voc_size + 1, self.embed_dim))
        model.add(Bidirectional(LSTM(128)))
        model.add(Dense(self.output_nodes, activation='softmax'))
        return model

    def ann_base(self, char_index):
        model = Sequential()
        voc_size = len(char_index.keys())
        model.add(Input(shape=(self.sequence_length,)))
        model.add(Embedding(voc_size + 1, self.embed_dim))
        model.add(Dense(128, activation='tanh'))
        model.add(Flatten())
        model.add(Dense(self.output_nodes, activation='softmax'))
        return model

    def cnn_base(self, char_index):
        model = Sequential()
        voc_size = len(char_index.keys())
        model.add(Input(shape=(self.sequence_length,)))
        model.add(Embedding(voc_size + 1, self.embed_dim))
        model.add(Conv1D(128, 3, activation='tanh'))
        model.add(Flatten())
        model.add(Dense(self.output_nodes, activation='softmax'))
        return model

    def cnn_complex(self, char_index):
        model = Sequential()
        voc_size = len(char_index.keys())
        
        model.add(Input(shape=(self.sequence_length,)))
        model.add(Embedding(voc_size + 1, self.embed_dim))

        model.add(Conv1D(128, 3, activation='relu'))
        model.add(BatchNormalization())
        model.add(MaxPooling1D(3))
        model.add(Dropout(0.15))

        model.add(Conv1D(128, 7, activation='relu', padding='same'))
        model.add(BatchNormalization())
        model.add(Dropout(0.15))

        model.add(Conv1D(128, 5, activation='relu', padding='same'))
        model.add(BatchNormalization())
        model.add(Dropout(0.15))

        model.add(Conv1D(128, 3, activation='relu', padding='same'))
        model.add(MaxPooling1D(3))
        model.add(Dropout(0.15))

        model.add(Bidirectional(LSTM(64)))
        model.add(Dense(self.output_nodes, activation='softmax'))
        return model