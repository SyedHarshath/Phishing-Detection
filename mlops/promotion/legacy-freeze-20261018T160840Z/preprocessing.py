from tensorflow.keras.preprocessing import sequence

SEQUENCE_LENGTH = 200

def preprocess_url(url,tokenizer):
    """
    Convert a raw URL into the format expected by the CNN-BiLSTM model.

    Pipeline:
        URL
        -> lowercase
        -> character/token encoding
        -> padding
    """
    url_lower = url.lower()
    seq = tokenizer.texts_to_sequences([url_lower])
    padded_seq = sequence.pad_sequences(seq,maxlen=SEQUENCE_LENGTH)
    return padded_seq