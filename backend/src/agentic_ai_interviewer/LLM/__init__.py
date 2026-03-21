import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv()


def get_llm(temperature: float = 0.5):
    """Returns a configured ChatGroq instance for high-speed inference."""
    return ChatGroq(
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        model_name="llama-3.3-70b-versatile",
        temperature=temperature,
    )
