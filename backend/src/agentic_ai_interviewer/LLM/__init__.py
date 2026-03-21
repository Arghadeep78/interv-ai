import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv()

def get_llm(temperature: float = 0.5):
    """Returns a configured ChatGroq instance for high-speed inference."""
    return ChatGroq(
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        model_name="llama3-8b-8192", # Using llama3-8b for fast concise output or llama3-70b-8192
        temperature=temperature,
    )
