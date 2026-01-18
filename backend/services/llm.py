import os
import logging
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("LLM")

# Configuración
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    logger.info("Gemini configurado con API KEY")

def generate_minutes(transcript_text, attendees_list, google_token=None, model_name=None):
    """
    Genera el acta usando la API de Gemini.
    Si se proporciona un google_token, se podría usar para validar identidad.
    """
    
    # Prioridad: 1. El modelo enviado por el frontend, 2. Variable de entorno, 3. Default
    # Usar models/gemini-2.0-flash como fallback
    MODEL_NAME = model_name or os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash")
    
    if google_token:
        logger.info("Solicitud de acta recibida con token de usuario Google")

    try:
        if not GOOGLE_API_KEY:
            return "Error: No se ha configurado la GOOGLE_API_KEY en el backend."
            
        # Validar y limpiar inputs
        if attendees_list is None:
            attendees_list = []
        safe_attendees = [str(a) for a in attendees_list if a]
        
        safe_transcript = str(transcript_text) if transcript_text else ""

        system_prompt = """
        Eres un secretario experto de un instituto de investigación (Instituto de Biotecnología).
        Tu tarea es redactar un ACTA DE REUNIÓN formal y profesional en formato Markdown.
        
        INSTRUCCIONES:
        1. Usa un tono formal, objetivo y conciso (tercera persona).
        2. Estructura el acta en: 
           - Encabezado (Fecha, Asistentes, Ausentes).
           - Orden del Día (deduce los puntos principales).
           - Desarrollo de la sesión (resumen por puntos).
           - Acuerdos y Votaciones (destaca claramente los resultados).
        3. NO inventes información. Básate solo en la transcripción.
        """

        user_prompt = f"""
        Asistentes oficiales: {", ".join(safe_attendees)}

        Transcripción de la reunión:
        ---
        {safe_transcript[:200000]} 
        ---
        
        Por favor, genera el acta ahora siguiendo el formato Markdown.
        """

        logger.info(f"Generando acta con {MODEL_NAME}...")
        model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=system_prompt
        )
        
        response = model.generate_content(
            user_prompt,
            generation_config={"temperature": 0.2}
        )
        
        return response.text

    except Exception as e:
        logger.error(f"Error en Gemini API: {e}")
        return f"Error generando acta: {str(e)}\n\n(Verifica que la clave de API sea válida y tenga permisos para Gemini)"
