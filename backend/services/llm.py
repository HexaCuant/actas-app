import os
import logging
from openai import OpenAI

logger = logging.getLogger("LLM")

# Configuración: Intenta leer API KEY de entorno, si no, usa una dummy para no romper la app
# En producción, esto debería ser obligatorio.
API_KEY = os.getenv("OPENAI_API_KEY", "sk-proj-dummy-key-replace-me") 

# Cliente OpenAI
# Si usas Ollama local: base_url="http://localhost:11434/v1", api_key="ollama"
client = OpenAI(api_key=API_KEY)

def generate_minutes(transcript_text, attendees_list):
    """
    Envía la transcripción completa al LLM para generar el acta.
    """
    
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
    4. Si hay votaciones, indica el resultado numérico si se menciona.
    5. Ignora comentarios irrelevantes o chistes.
    """

    user_prompt = f"""
    Aquí tienes la lista de asistentes oficiales:
    {", ".join(attendees_list)}

    Aquí tienes la transcripción de la reunión (los nombres de los hablantes han sido verificados):
    
    --- INICIO TRANSCRIPCIÓN ---
    {transcript_text[:100000]} 
    --- FIN TRANSCRIPCIÓN ---
    
    (Nota: Si la transcripción es muy larga, ha sido truncada. Prioriza el inicio y los acuerdos finales).
    
    Por favor, genera el acta ahora.
    """

    try:
        if API_KEY.startswith("sk-proj-dummy"):
            return "# ACTA DE EJEMPLO (SIN API KEY)\n\n**Nota:** Configura tu `OPENAI_API_KEY` para generar actas reales.\n\n## 1. Asistentes\n..."

        logger.info("Enviando petición a LLM...")
        response = client.chat.completions.create(
            model="gpt-4o-mini", # O "llama3" si usas local
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3, # Baja temperatura para ser factual
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"Error LLM: {e}")
        return f"Error generando acta: {str(e)}"
