import torch

# HACK: Forzar weights_only=False siempre
# PyTorch 2.6 rompe la compatibilidad con checkpoints antiguos (whisperx/pyannote)
# Al interceptar torch.load y borrar 'weights_only', forzamos el comportamiento antiguo inseguro pero funcional.
_original_load = torch.load
def _safe_load(*args, **kwargs):
    # Borramos explícitamente weights_only para que use el default antiguo (o lo forzamos a False)
    # Sin embargo, si la firma original lo tiene a True por defecto, hay que pasarlo a False.
    kwargs['weights_only'] = False
    return _original_load(*args, **kwargs)
torch.load = _safe_load

# HACK 2: Permitir la clase que falla específicamente
try:
    import omegaconf
    torch.serialization.add_safe_globals([omegaconf.listconfig.ListConfig])
except ImportError:
    pass # Si omegaconf no está instalado, no podemos añadirlo, pero el hack 1 debería bastar.
except AttributeError:
    pass # Si torch es muy viejo y no tiene add_safe_globals

import whisperx
import cv2
import gc
import os
import json
import logging
import re

# Configuración de Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= CONFIGURACIÓN DEFAULT =================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
BATCH_SIZE = 16
COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"
OCR_ENGINE = "easyocr" 

# Inicializar OCR Reader globalmente (lazy load si se prefiere, pero aquí lo haremos global)
ocr_reader = None

def init_ocr():
    global ocr_reader
    if ocr_reader is not None:
        return

    if OCR_ENGINE == "paddle":
        try:
            from paddleocr import PaddleOCR
            ocr_reader = PaddleOCR(use_angle_cls=True, lang='es', show_log=False)
        except ImportError:
            logger.error("PaddleOCR no instalado.")
    elif OCR_ENGINE == "easyocr":
        try:
            import easyocr
            # Inicializa EasyOCR una sola vez
            ocr_reader = easyocr.Reader(['es'], gpu=(DEVICE=="cuda"))
        except ImportError:
            logger.error("EasyOCR no instalado.")

def load_hf_token(filepath):
    try:
        with open(filepath, 'r') as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            if not lines: raise ValueError("Archivo vacío")
            return lines[0]
    except Exception as e:
        logger.error(f"ERROR leyendo token en {filepath}: {e}")
        return None

def transcribe_audio(video_path, hf_token):
    logger.info(f"--- 1. Iniciando WhisperX en {DEVICE} ---")
    
    # 1. Transcribir
    logger.info("Cargando modelo transcripción...")
    model = whisperx.load_model("large-v3", DEVICE, compute_type=COMPUTE_TYPE)
    audio = whisperx.load_audio(video_path)
    result = model.transcribe(audio, batch_size=BATCH_SIZE)
    
    del model
    gc.collect()
    torch.cuda.empty_cache()
    
    # 2. Alinear
    logger.info("Alineando...")
    model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=DEVICE)
    result = whisperx.align(result["segments"], model_a, metadata, audio, DEVICE, return_char_alignments=False)
    
    del model_a
    gc.collect()
    torch.cuda.empty_cache()
    
    # 3. Diarizar
    logger.info("Diarizando...")
    from whisperx.diarize import DiarizationPipeline
    diarize_model = DiarizationPipeline(use_auth_token=hf_token, device=DEVICE)
    diarize_segments = diarize_model(audio)
    result = whisperx.assign_word_speakers(diarize_segments, result)
    
    del diarize_model
    gc.collect()
    torch.cuda.empty_cache()
    
    return result

def preprocess_image(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh

def normalize_name(text):
    replacements = {'?': 'í', '_': ' ', '|': 'l'}
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split()
    normalized_words = []
    for word in words:
        if word.lower() in ['de', 'del', 'la', 'las', 'los', 'y'] and normalized_words:
            normalized_words.append(word.lower())
        else:
            normalized_words.append(word.capitalize())
    return ' '.join(normalized_words)

def is_valid_name(text):
    text_upper = text.upper().strip()
    if len(text) < 3: return False
    
    forbidden = [
        "DIRECTO", "VIVO", "NEWS", "NOTICIAS", "GMT", "CET", 
        "HORA", "SUBSCRIBE", "CANAL", "WWW", ".COM", "LIVE",
        "QUEJA", "SUGERENCIA", "ACCESIB", "CONDICION", "LEGAL",
        "MAPA", "WEB", "COOKIE", "PRIVACIDAD", "CONTACTO",
        "AVISO", "POLÍTICA", "DERECHOS", "RESERV",
        "MUTE", "UNMUTE", "SHARE", "SCREEN", "CHAT", "PARTICIPANTS",
        "RECORDING", "GRABANDO", "SILENCIAR", "COMPARTIR",
        "ADMINISTR", "TELÉFONO", "FAX", "UNIVERSIDAD", "GRANADA",
        "DIRECCIÓN", "SECRETAR", "BIOTECNOL", "NOTICIAS", "POLITICA",
        "NOT AVAILABLE", "NAME NOT", "AVAILABLE",
        "COORDINADOR", "DIRECTOR", "MASTER", "MÁSTER", "PROFESOR",
    ]
    if any(bad in text_upper for bad in forbidden): return False
    
    digit_count = sum(c.isdigit() for c in text)
    if digit_count > 3: return False
    if len(text) > 50: return False
    if not any(c.isupper() for c in text): return False
    
    words = text.strip().split()
    if len(words) < 2: return False
    
    return True

def extract_text_from_frame(frame):
    h, w, _ = frame.shape
    # Zonas configuradas (tercio inferior)
    zonas = [(0.85, 0.98, 0.0, 1.0)]
    
    all_texts = []
    for (top_pct, bottom_pct, left_pct, right_pct) in zonas:
        y1, y2 = int(h * top_pct), int(h * bottom_pct)
        x1, x2 = int(w * left_pct), int(w * right_pct)
        
        zona = frame[y1:y2, x1:x2]
        if zona.size == 0: continue
        
        if OCR_ENGINE == "easyocr":
            if ocr_reader is None: init_ocr()
            result = ocr_reader.readtext(zona)
            for text in result:
                if text[2] > 0.5 and len(text[1]) > 5 and len(text[1]) < 50:
                    all_texts.append(text[1])
    
    nombres_validos = [t for t in all_texts if is_valid_name(t)]
    if nombres_validos:
        return max(nombres_validos, key=len)
    return ""

def identify_speakers_visually(video_path, segments, debug_dir=None):
    logger.info(f"--- 2. Identificando Hablantes (Estrategia Multi-Frame) ---")
    cap = cv2.VideoCapture(video_path)
    speaker_map = {} 
    
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
    
    for segment in segments:
        if "speaker" not in segment: continue
        speaker_id = segment["speaker"]
        
        if speaker_id in speaker_map:
            segment["speaker"] = speaker_map[speaker_id]
            continue
            
        duration = segment["end"] - segment["start"]
        check_points = [
            segment["start"] + 1.0,
            segment["start"] + 3.0,
            segment["start"] + (duration / 2)
        ]
        
        found_name = None
        for t in check_points:
            if t > segment["end"]: continue 
            
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret: continue
            
            raw_text = extract_text_from_frame(frame)
            
            if raw_text and is_valid_name(raw_text):
                normalized = normalize_name(raw_text)
                logger.info(f" -> HALLAZGO para {speaker_id} en {t:.1f}s: '{normalized}'")
                found_name = normalized
                
                if debug_dir:
                    filename = f"{speaker_id}_{normalized.replace(' ', '_')}.jpg"
                    cv2.imwrite(os.path.join(debug_dir, filename), frame)
                break 
        
        if found_name:
            speaker_map[speaker_id] = found_name
            segment["speaker"] = found_name
            
    cap.release()
    return segments, speaker_map

def process_meeting_video(video_path, token_file_path):
    """Función principal llamada por la API"""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video no encontrado: {video_path}")

    hf_token = load_hf_token(token_file_path)
    if not hf_token:
        raise ValueError("Token no válido o no encontrado")
        
    init_ocr()

    # Fase 1: Audio
    transcript = transcribe_audio(video_path, hf_token)
    
    # Fase 2: Video
    # Usar un directorio de debug temporal relativo al video
    debug_dir = os.path.join(os.path.dirname(video_path), "debug_frames")
    final_segments, speaker_map = identify_speakers_visually(video_path, transcript["segments"], debug_dir=debug_dir)
    
    return {
        "segments": final_segments,
        "speakers_found": speaker_map,
        "language": transcript.get("language", "es") # Fallback seguro
    }
