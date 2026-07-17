import json
import requests
import numpy as np
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"

ledger_path = Path("../data/memory/ledger/identity_pack.jsonl")
index_path = Path("../data/memory/index/identity_embeddings.npy")
text_store_path = Path("../data/memory/index/identity_texts.json")

texts = []
with open(ledger_path) as f:
    for line in f:
        obj = json.loads(line)
        texts.append(obj["text"])

embeddings = []
for text in texts:
    response = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": text})
    embeddings.append(response.json()["embedding"])

embeddings = np.array(embeddings)
np.save(index_path, embeddings)

with open(text_store_path, "w") as f:
    json.dump(texts, f)

print("Identity index built using Ollama embeddings.")
