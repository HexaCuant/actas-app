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
TOKEN_FILE = "../../token-huggingface"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SESSIONS_DIR, exist_ok=True)

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

# Base de datos en memoria
jobs_db = {}

# --- FUNCIONES AUXILIARES ---
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
    if job_id not in jobs_db or jobs_db[job_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not ready or not found")
    
    job = jobs_db[job_id]
    speaker_mapping = payload.get("speaker_mapping", {})
    segments = job["result"]["segments"]
    full_transcript = []
    
    for seg in segments:
        # Lógica de fallback para nombres, replicando la del frontend si es necesario
        # Pero aquí asumimos que el mapping ya tiene la decisión final
        original_speaker = seg["speaker"]
        final_name = speaker_mapping.get(original_speaker, original_speaker)
        if not final_name: final_name = "Desconocido"
        full_transcript.append(f"{final_name}: {seg['text'].strip()}")
    
    transcript_text = "\n".join(full_transcript)
    attendees_list = job.get("attendees", [])
    
    try:
        minutes_md = generate_minutes(transcript_text, attendees_list)
        return {"minutes": minutes_md}
    except Exception as e:
        logger.error(f"Error generando acta: {e}")
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
    """Lista las sesiones guardadas en el servidor"""
    logger.info(f"Listando sesiones en: {SESSIONS_DIR}")
    files = glob.glob(os.path.join(SESSIONS_DIR, "*.json"))
    logger.info(f"Archivos encontrados: {files}")
    # Devolver nombre base y fecha mod
    sessions = []
    for f in files:
        name = os.path.basename(f).replace(".json", "")
        mtime = os.path.getmtime(f)
        sessions.append({"name": name, "timestamp": mtime})
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
