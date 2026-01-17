# 🎙️ AI Minutes Manager (Gestor de Actas con IA)

Una aplicación full-stack diseñada para automatizar la creación de actas de reuniones mediante Inteligencia Artificial. Combina transcripción de audio, diarización de hablantes y reconocimiento visual (OCR) para ofrecer una experiencia de edición fluida y profesional.

## ✨ Características Principales

- **Transcripción Inteligente:** Utiliza **WhisperX** para una transcripción rápida y precisa con alineación de palabras.
- **Diarización de Hablantes:** Identifica quién habla en cada momento.
- **Identificación Visual (OCR):** Procesa el video para detectar nombres en pantalla (EasyOCR) y sugerir automáticamente quién es cada hablante.
- **Editor en Tiempo Real:** Interfaz intuitiva para corregir nombres de hablantes (global o individualmente) y textos.
- **Herramienta de Recorte (Trim):** Recorta partes innecesarias del video directamente desde la app con sincronización automática de la transcripción.
- **Generación de Actas con LLM:** Genera resúmenes formales y actas estructuradas utilizando modelos de lenguaje (OpenAI).
- **Gestión de Sesiones:** Guarda y reanuda tu trabajo en cualquier momento.
- **Exportación:** Descarga la transcripción corregida en formato `.txt`.

## 🛠️ Tecnologías Utilizadas

- **Backend:** Python, FastAPI, WhisperX, PyTorch, EasyOCR, FFmpeg.
- **Frontend:** React, Vite, Tailwind CSS, Lucide-React.
- **IA:** Modelos de OpenAI (GPT), WhisperX para ASR.

## 🚀 Instalación y Uso

### Requisitos Previos
- Python 3.9+
- Node.js & npm
- **FFmpeg** instalado en el sistema.
- GPU recomendada (para transcripción rápida).

### Backend
1. Navega a `backend/`.
2. Instala dependencias: `pip install -r requirements.txt`.
3. Ejecuta el servidor: `python -m uvicorn main:app --reload`.

### Frontend
1. Navega a `frontend/`.
2. Instala dependencias: `npm install`.
3. Inicia la app: `npm run dev`.

## 📁 Estructura del Proyecto
- `/backend`: Servidor API y lógica de procesamiento de IA.
- `/frontend`: Interfaz de usuario moderna en React.
- `/uploads`: Almacenamiento temporal de videos y archivos procesados (ignorado en git).
- `/sessions`: Archivos JSON con el estado de las sesiones guardadas.

## 📄 Licencia
Este proyecto es de uso interno / educacional.

---
Desarrollado con ❤️ para la gestión eficiente de reuniones.
