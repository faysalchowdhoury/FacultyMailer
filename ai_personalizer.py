import os
from google import genai
from google.genai import types

class AIPersonalizer:
    # gemini-2.5-flash is being retired for new users (returns 404 NOT_FOUND).
    # Current fast model as of 2026. Override via the model argument if this changes.
    DEFAULT_MODEL = "gemini-3.5-flash"

    def __init__(self, api_key: str, model: str = None):
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.client = genai.Client(api_key=api_key)

    def generate_custom_hook(self, professor_name: str, bio_text: str, applicant_research_summary: str = "") -> str:
        """
        Generates a 1-2 sentence personalized research connection for a professor based on their profile text.
        """
        if not bio_text or len(bio_text.strip()) < 40:
            return ""

        if not applicant_research_summary:
            applicant_research_summary = (
                "Predictive energy and vehicle emissions modeling, machine learning surrogates, "
                "EV charging demand analysis, and transportation electrification pathways."
            )

        prompt = f"""
        You are assisting a PhD graduate writing a brief, professional inquiry email to Professor {professor_name}.
        
        APPLICANT BACKGROUND:
        {applicant_research_summary}
        
        PROFESSOR BIO / RESEARCH TEXT:
        {bio_text[:3000]}
        
        TASK:
        Write exactly 1-2 professional sentences connecting the applicant's background with the professor's research focus.
        
        RULES:
        1. Be specific, natural, and concise.
        2. Do NOT include generic filler like "I am writing to..." or "I hope this email finds you well."
        3. Do NOT use buzzwords or overly dramatic language.
        4. Return ONLY the 1-2 sentence connection string itself.
        """

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=150
                )
            )
            return response.text.strip()
        except Exception as e:
            print(f"AI Personalization note for {professor_name}: {e}")
            return ""