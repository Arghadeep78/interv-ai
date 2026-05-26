import os
import shutil
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document


def get_embeddings():
    """Returns the HuggingFace embedding model used across the application."""
    return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")


def get_faiss_path(session_id: str) -> str:
    """Returns the absolute disk path for a session's FAISS index."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    path = os.path.join(base_dir, "faiss_store", session_id)
    os.makedirs(path, exist_ok=True)
    return path


def delete_faiss_index(session_id: str) -> None:
    """
    Removes a session's on-disk FAISS index. Called once the interview report
    is generated so completed sessions don't accumulate on disk forever.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    path = os.path.join(base_dir, "faiss_store", session_id)
    shutil.rmtree(path, ignore_errors=True)


def save_faiss_index(docs: list[Document], session_id: str) -> FAISS:
    """Creates a new FAISS index from documents and saves it to disk."""
    embeddings = get_embeddings()
    vectorstore = FAISS.from_documents(docs, embeddings)
    vectorstore.save_local(get_faiss_path(session_id))
    return vectorstore


def load_faiss_index(session_id: str) -> FAISS:
    """Loads a FAISS index from disk for a given session."""
    embeddings = get_embeddings()
    # allow_dangerous_deserialization unpickles the stored index. This is safe
    # here because the only writer is our own save_faiss_index / add_documents_to_index,
    # and the path is a UUID controlled entirely by the server — never user-supplied.
    return FAISS.load_local(
        get_faiss_path(session_id),
        embeddings,
        allow_dangerous_deserialization=True,
    )


def add_documents_to_index(docs: list[Document], session_id: str) -> FAISS:
    """
    Merges new documents into an existing FAISS index and re-saves.
    Used to dynamically augment the knowledge base with Tavily search results.
    """
    embeddings = get_embeddings()
    path = get_faiss_path(session_id)

    try:
        existing = FAISS.load_local(
            path, embeddings, allow_dangerous_deserialization=True  # safe — server-written UUID path
        )
        new_store = FAISS.from_documents(docs, embeddings)
        existing.merge_from(new_store)
        existing.save_local(path)
        return existing
    except Exception:
        # If no existing index, create from scratch
        return save_faiss_index(docs, session_id)