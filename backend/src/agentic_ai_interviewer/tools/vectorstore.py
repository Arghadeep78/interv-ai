import os
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

def get_embeddings():
    return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

def get_faiss_path(session_id: str) -> str:
    path = os.path.join("faiss_store", session_id)
    os.makedirs(path, exist_ok=True)
    return path

def save_faiss_index(docs: list[Document], session_id: str):
    """Creates a new FAISS index from documents and saves it to disk."""
    embeddings = get_embeddings()
    vectorstore = FAISS.from_documents(docs, embeddings)
    vectorstore.save_local(get_faiss_path(session_id))
    return vectorstore

def load_faiss_index(session_id: str) -> FAISS:
    """Loads a FAISS index from disk for a given session."""
    embeddings = get_embeddings()
    return FAISS.load_local(
        get_faiss_path(session_id), 
        embeddings, 
        allow_dangerous_deserialization=True # Necessary for local loading in trusted environment
    )