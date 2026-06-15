# llm.py — LLM integration for the SpO2 chat page.
#
# This is the ONE place where Groq is called.  To swap providers (OpenAI,
# Ollama, etc.) edit only this file — nothing else needs to change.
#
# The GROQ_API_KEY is loaded from .env (copy .env.example → .env and fill it in).
# The GROQ_MODEL env var lets you switch models without editing code.

import json
import os

from dotenv import load_dotenv

# Load the .env file from the project root (the folder one level above this file)
load_dotenv()


def ask_llm(question: str, night_summary: dict) -> str:
    """
    Send a question plus the night summary to the Groq LLM and return the answer.

    Args:
        question      — the user's free-text question (e.g. "Why did my SpO2 drop?")
        night_summary — the §5 summary dict (event_list stripped before calling)

    Returns:
        The LLM's answer as a plain string.
        If the API key is missing or the call fails, returns a clear fallback message.
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return (
            "The LLM is not configured. To enable chat:\n"
            "1. Copy .env.example to .env\n"
            "2. Replace 'your_groq_api_key_here' with your real Groq API key\n"
            "3. Restart the server\n\n"
            "This is a screening estimate only — please consult a sleep specialist "
            "for a proper diagnosis."
        )

    # Which Groq model to use.  Override with GROQ_MODEL= in your .env.
    # llama3-8b-8192 is fast and free-tier friendly.
    model = os.getenv("GROQ_MODEL", "llama3-8b-8192")

    # Build a compact summary string for the prompt.
    # We skip event_list (can be 400+ entries) to keep token count low.
    compact = {
        k: v for k, v in night_summary.items()
        if k not in ("event_list",)  # hourly is small enough to include
    }

    system_prompt = (
        "You are a sleep-health screening assistant. "
        "A patient just recorded an overnight SpO2 (blood-oxygen) session "
        "and wants to understand the results. "
        "Answer in plain, friendly language that someone with no medical background "
        "can understand. Do not make a diagnosis — only explain what the screening "
        "data shows. Keep answers concise (2–4 sentences). "
        "Always end your answer with this exact sentence on its own line: "
        "'This is a screening estimate only — please consult a sleep specialist "
        "for a proper diagnosis.'\n\n"
        f"Night summary:\n{json.dumps(compact, indent=2)}"
    )

    try:
        from groq import Groq   # imported here so the module loads even without groq installed
        client   = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system",  "content": system_prompt},
                {"role": "user",    "content": question},
            ],
            max_tokens=512,
            temperature=0.3,   # lower = more factual, less creative
        )
        return response.choices[0].message.content

    except Exception as exc:
        return (
            f"Could not reach the LLM ({exc}). "
            "Check your GROQ_API_KEY and internet connection. "
            "This is a screening estimate only — please consult a sleep specialist "
            "for a proper diagnosis."
        )
