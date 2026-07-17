import numpy as np
import json
import requests

OLLAMA_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"

identity_embeddings = np.load("../data/memory/index/identity_embeddings.npy")
identity_texts = json.load(open("../data/memory/index/identity_texts.json"))

def retrieve_identity(query, top_k=5):
    response = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": query})
    q_emb = np.array(response.json()["embedding"])

    scores = identity_embeddings @ q_emb
    top_indices = np.argsort(scores)[-top_k:][::-1]
    return [identity_texts[i] for i in top_indices]
