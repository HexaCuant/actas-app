from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.engine import process_meeting_video
from services.llm import generate_minutes
import shutil
import os
import uuid
import logging
import json
import pandas as pd
from typing import Optional, Dict
import glob
import subprocess
from pathlib import Path
from pydantic import BaseModel

class TrimRequest(BaseModel):
    video_url: str
    new_name: str
    start: float
    end: float

# Configuración
UPLOAD_DIR = os.path.abspath("../uploads") 
SESSIONS_DIR = os.path.abspath("../sessions") # Nuevo directorio para sesiones
ACTAS_DIR = os.path.abspath("../actas")  # Directorio para actas generadas
TOKEN_FILE = "../../token-huggingface"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(ACTAS_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("API")

app = FastAPI(title="Actas Web API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"], 
)

# Servir videos subidos 
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="uploads")
# Servir actas generadas
app.mount("/actas", StaticFiles(directory=ACTAS_DIR), name="actas")

# Base de datos en memoria
jobs_db = {}

# --- FUNCIONES AUXILIARES ---

def save_acta_files(session_name: str, minutes_md: str) -> dict:
    """Guarda el acta en markdown y la convierte a PDF con pandoc"""
    # Limpiar nombre de archivo
    safe_name = "".join([c for c in session_name if c.isalnum() or c in (' ', '-', '_')]).strip()
    if not safe_name:
        safe_name = "acta_sin_nombre"
    
    md_filename = f"acta_{safe_name}.md"
    pdf_filename = f"acta_{safe_name}.pdf"
    
    md_path = os.path.join(ACTAS_DIR, md_filename)
    pdf_path = os.path.join(ACTAS_DIR, pdf_filename)
    
    result = {"md": None, "pdf": None}
    
    try:
        # Guardar markdown
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(minutes_md)
        result["md"] = f"/actas/{md_filename}"
        logger.info(f"Acta markdown guardada: {md_path}")
        
        # Convertir a PDF con pandoc
        try:
            subprocess.run([
                "pandoc", md_path, 
                "-o", pdf_path,
                "--pdf-engine=xelatex",
                "-V", "geometry:margin=2.5cm",
                "-V", "mainfont:DejaVu Sans",
                "-V", "fontsize=11pt",
                "--toc",
                "--toc-depth=2"
            ], check=True, capture_output=True, text=True)
            result["pdf"] = f"/actas/{pdf_filename}"
            logger.info(f"Acta PDF generada: {pdf_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error convirtiendo a PDF: {e.stderr}")
        except FileNotFoundError:
            logger.warning("pandoc no encontrado, saltando conversión a PDF")
    except Exception as e:
        logger.error(f"Error guardando acta: {e}")
    
    return result

def parse_attendees(file_path):
    """Lee el Excel e intenta reconstruir nombres completos de forma inteligente"""
    try:
        df = pd.read_excel(file_path)
        df.columns = [str(c).strip() for c in df.columns]
        normalized_cols = {c.lower(): c for c in df.columns}
        
        logger.info(f"Columnas encontradas en Excel: {list(df.columns)}")
        
        name_col = None
        surname_col = None
        
        posibles_apellidos = ['apellidos', 'apellido', 'surname', 'surnames', 'last name', 'lastname', 'primer apellido']
        for cand in posibles_apellidos:
            match = next((c for c in normalized_cols if cand == c or cand in c), None)
            if match:
                surname_col = normalized_cols[match]
                break

        posibles_nombres = ['nombre', 'nombres', 'name', 'firstname', 'first name']
        for cand in posibles_nombres:
             match = next((c for c in normalized_cols if cand == c or cand in c), None)
             if match and match != surname_col:
                 name_col = normalized_cols[match]
                 break
        
        full_names = []
        
        if name_col and surname_col and name_col != surname_col:
            for _, row in df.iterrows():
                n = str(row[name_col]).strip()
                s = str(row[surname_col]).strip()
                if n.lower() in ['nan', 'nat'] : n = ""
                if s.lower() in ['nan', 'nat'] : s = ""
                if n and s: full_names.append(f"{n} {s}")
                elif n: full_names.append(n)
                elif s: full_names.append(s)
        elif name_col or surname_col:
            target = name_col or surname_col
            full_names = [n.strip() for n in df[target].dropna().astype(str).tolist()]
        else:
            for col in df.columns:
                sample = df[col].dropna().head(5).astype(str).tolist()
                if sum(not s.isnumeric() for s in sample) >= len(sample) * 0.8:
                    full_names = df[col].dropna().astype(str).tolist()
                    break

        return sorted(list(set([n.title() for n in full_names if n and n.lower() != "nan" and len(n) > 2])))
    except Exception as e:
        logger.error(f"Error leyendo Excel: {e}")
        return []

def task_process_video(job_id: str, video_path: str, attendees_path: Optional[str] = None):
    try:
        jobs_db[job_id]["status"] = "processing"
        attendees_list = []
        if attendees_path:
            attendees_list = parse_attendees(attendees_path)
            jobs_db[job_id]["attendees"] = attendees_list
        
        abs_token_path = os.path.abspath(TOKEN_FILE)
        result = process_meeting_video(video_path, abs_token_path)
        
        jobs_db[job_id]["result"] = result
        jobs_db[job_id]["status"] = "completed"
    except Exception as e:
        logger.error(f"Error en job {job_id}: {e}")
        jobs_db[job_id]["status"] = "failed"
        jobs_db[job_id]["error"] = str(e)

# --- ENDPOINTS ---

@app.post("/upload")
async def upload_video(
    file: UploadFile = File(...), 
    attendees: Optional[UploadFile] = File(None),
    background_tasks: BackgroundTasks = None
):
    # Usar nombre de archivo original para evitar duplicados (limpiando espacios)
    safe_filename = file.filename.replace(" ", "_")
    video_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    with open(video_path, "wb+") as f:
        shutil.copyfileobj(file.file, f)
        
    # El job_id sigue siendo único para la sesión actual de procesamiento
    job_id = str(uuid.uuid4())
    
    attendees_path = None
    if attendees:
        attendees_filename = f"attendees_{safe_filename}.xlsx"
        attendees_path = os.path.join(UPLOAD_DIR, attendees_filename)
        with open(attendees_path, "wb+") as f:
            shutil.copyfileobj(attendees.file, f)
            
    jobs_db[job_id] = {
        "status": "queued",
        "video_filename": safe_filename,
        "path": video_path
    }
    background_tasks.add_task(task_process_video, job_id, video_path, attendees_path)
    return {"job_id": job_id, "status": "queued"}

@app.post("/generate-minutes/{job_id}")
async def api_generate_minutes(job_id: str, payload: Dict = Body(...)):
    try:
        # Intentar obtener segmentos directamente del payload (más robusto para sesiones cargadas)
        segments = payload.get("segments")
        attendees_list = payload.get("attendees", [])
        speaker_mapping = payload.get("speaker_mapping", {})
        if not isinstance(speaker_mapping, dict):
            speaker_mapping = {}
        
        # Si no vienen en el payload, buscarlos en la DB en memoria (para sesiones recién procesadas)
        if not segments:
            if job_id not in jobs_db or jobs_db[job_id]["status"] != "completed":
                raise HTTPException(status_code=400, detail="Sesión no lista o datos faltantes en la petición")
            job = jobs_db[job_id]
            segments = job["result"]["segments"]
            attendees_list = job.get("attendees", [])

        full_transcript = []
        
        # Debug: Verificar estructura de segments
        logger.info(f"Procesando {len(segments)} segmentos para el acta")

        for i, seg in enumerate(segments):
            try:
                # Asegurar que seg es un dict
                if not isinstance(seg, dict):
                    logger.warning(f"Segmento {i} no es un diccionario: {type(seg)}")
                    continue
                    
                # Obtener speaker de forma segura
                original_speaker = seg.get("speaker") if "speaker" in seg else "Desconocido"
                if original_speaker is None:
                    original_speaker = "Desconocido"
                    
                final_name = speaker_mapping.get(original_speaker, original_speaker)
                if not final_name: 
                    final_name = "Desconocido"
                
                text = seg.get("text", "").strip()
                if text:  # Solo añadir si hay texto
                    full_transcript.append(f"{final_name}: {text}")
            except Exception as e:
                logger.error(f"Error procesando segmento {i}: {e}")
                continue
        
        transcript_text = "\n".join(full_transcript)
        
        google_token = payload.get("google_user_token")
        model_name = payload.get("model", "gemini-2.0-flash-exp")
        session_name = payload.get("session_name", job_id)  # Nombre de la sesión para guardar el acta
        
        minutes_md = generate_minutes(transcript_text, attendees_list, google_token, model_name)
        
        # Guardar el acta en markdown y convertir a PDF
        acta_files = save_acta_files(session_name, minutes_md)
        
        return {"minutes": minutes_md, "acta_files": acta_files}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generando acta: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs_db[job_id]
    if job["status"] == "completed":
        return {
            "status": "completed",
            "result": job["result"],
            "attendees": job.get("attendees", []),
            "video_url": f"/files/{job['video_filename']}"
        }
    return {"status": job["status"], "error": job.get("error")}

# --- ENDPOINTS DE SESIÓN ---

@app.get("/sessions")
def list_sessions():
    """Lista las sesiones guardadas en el servidor con sus actas asociadas"""
    logger.info(f"Listando sesiones en: {SESSIONS_DIR}")
    files = glob.glob(os.path.join(SESSIONS_DIR, "*.json"))
    logger.info(f"Archivos encontrados: {files}")
    # Devolver nombre base y fecha mod
    sessions = []
    for f in files:
        name = os.path.basename(f).replace(".json", "")
        mtime = os.path.getmtime(f)
        
        # Buscar actas asociadas
        acta_md = os.path.join(ACTAS_DIR, f"acta_{name}.md")
        acta_pdf = os.path.join(ACTAS_DIR, f"acta_{name}.pdf")
        
        session_data = {
            "name": name, 
            "timestamp": mtime,
            "acta_md": f"/actas/acta_{name}.md" if os.path.exists(acta_md) else None,
            "acta_pdf": f"/actas/acta_{name}.pdf" if os.path.exists(acta_pdf) else None
        }
        sessions.append(session_data)
    # Ordenar por más reciente primero
    sessions.sort(key=lambda x: x["timestamp"], reverse=True)
    return sessions

@app.post("/sessions")
def save_session(payload: Dict = Body(...)):
    """Guarda una sesión en el servidor"""
    name = payload.get("name", "sin_titulo").strip()
    # Limpiar nombre de archivo (básico)
    safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
    if not safe_name: safe_name = "session_unnamed"
    
    file_path = os.path.join(SESSIONS_DIR, f"{safe_name}.json")
    
    # Guardar datos
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(payload["data"], f, ensure_ascii=False, indent=2)
        return {"message": "Sesión guardada", "filename": safe_name}
    except Exception as e:
        logger.error(f"Error guardando sesión: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions/{name}")
def load_session(name: str):
    """Carga una sesión específica"""
    file_path = os.path.join(SESSIONS_DIR, f"{name}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.error(f"Error cargando sesión: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT TRIM ---
@app.post("/trim-video")
async def trim_video(request: TrimRequest):
    try:
        # Debug: Imprimir qué recibimos
        logger.info(f"Trim request recibido: {request}")
        
        input_path = Path(UPLOAD_DIR) / Path(request.video_url).name
        
        # Asegurar que el nombre de salida tenga extensión
        new_name = request.new_name
        if not new_name.lower().endswith(('.mp4', '.webm', '.mov', '.avi')):
            ext = input_path.suffix or ".mp4"
            new_name = f"{new_name}{ext}"
            
        output_filename = f"trimmed_{new_name}"
        output_path = Path(UPLOAD_DIR) / output_filename
        
        # Debug: Verificar que el archivo existe
        if not input_path.exists():
            logger.error(f"Archivo de entrada no encontrado: {input_path}")
            raise Exception(f"Archivo no encontrado: {input_path}")
        
        logger.info(f"Archivo encontrado: {input_path}")
        logger.info(f"Tamaño: {input_path.stat().st_size / (1024*1024):.1f} MB")
        
        start_time = request.start
        end_time = request.end - request.start
        
        # Usar ruta absoluta de ffmpeg para evitar problemas de PATH
        ffmpeg_path = shutil.which("ffmpeg") or "/bin/ffmpeg"
        
        cmd = [
            ffmpeg_path,
            "-ss", str(start_time),
            "-i", str(input_path),
            "-t", str(end_time),   # Usamos -t para la duración exacta
            "-c:v", "libx264",     # Re-codificar video para sincronización perfecta
            "-preset", "ultrafast",# Máxima velocidad de codificación
            "-crf", "23",          # Calidad balanceada
            "-c:a", "aac",         # Re-codificar audio
            "-y",
            str(output_path)
        ]
        
        logger.info(f"Ejecutando Trim: {' '.join(cmd)}")
        
        # Ejecutar con timeout y mejor logging
        try:
            process = subprocess.run(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True,
                timeout=300  # 5 minutos máximo
            )
            
            logger.info(f"FFmpeg stdout: {process.stdout}")
            if process.stderr:
                logger.info(f"FFmpeg stderr: {process.stderr}")
            
            if process.returncode != 0:
                logger.error(f"FFmpeg falló con código {process.returncode}")
                logger.error(f"FFmpeg Error: {process.stderr}")
                raise Exception(f"FFmpeg falló: {process.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timeout después de 5 minutos")
            raise Exception("El recorte está tardando demasiado (timeout)")
        
        # Verificar que el archivo de salida existe y tiene contenido
        import time
        for _ in range(5): # Reintentar durante 1 segundo si es necesario
            if output_path.exists() and output_path.stat().st_size > 0:
                break
            time.sleep(0.2)
        else:
            logger.error(f"Archivo de salida no creado o vacío: {output_path}")
            raise Exception("No se pudo crear el archivo recortado correctamente")
        
        logger.info(f"Archivo recortado creado y verificado: {output_path}")
        logger.info(f"Tamaño final: {output_path.stat().st_size / (1024*1024):.1f} MB")
            
        return {
            "message": "Video recortado con éxito",
            "new_video_url": f"/files/{output_filename}",
            "original_start": start_time # Para que el frontend sepa cuánto restar a los tiempos
        }
        
    except Exception as e:
        logger.error(f"Error recortando video: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return {"message": "API de Actas funcionando"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
