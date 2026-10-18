import json
from pathlib import Path

from tensorflow.keras.preprocessing.text import Tokenizer


# ---------------------------------------------------------
# Project paths
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATASET_DIR = PROJECT_ROOT / "dataset" / "big_dataset"
OUTPUT_PATH = PROJECT_ROOT / "ml" / "artifacts" / "tokenizer.json"


# ---------------------------------------------------------
# Make sure output directory exists
# ---------------------------------------------------------

OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------
# Load URLs in the EXACT same order used during training
# ---------------------------------------------------------

urls = []

for filename in ["train.txt", "val.txt", "test.txt"]:

    file_path = DATASET_DIR / filename

    if not file_path.exists():
        raise FileNotFoundError(
            f"Dataset file not found: {file_path}"
        )

    with open(file_path, "r", encoding="utf-8") as f:

        for line in f:

            parts = line.strip().split("\t")

            if len(parts) == 2:
                urls.append(parts[1])


print(f"Total URLs used to build tokenizer: {len(urls)}")


# ---------------------------------------------------------
# Create tokenizer EXACTLY as in original training code
# ---------------------------------------------------------

tokenizer = Tokenizer(
    lower=True,
    char_level=True,
    oov_token="-n-"
)


# ---------------------------------------------------------
# Fit tokenizer
# ---------------------------------------------------------

tokenizer.fit_on_texts(urls)


# ---------------------------------------------------------
# Save tokenizer as JSON
# ---------------------------------------------------------

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write(tokenizer.to_json())


print(f"Tokenizer saved to:")
print(OUTPUT_PATH)

print(f"Vocabulary size: {len(tokenizer.word_index)}")