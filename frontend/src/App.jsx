import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
 import { Upload, FileVideo, FileSpreadsheet, CheckCircle, Loader2, Play, User, FileText, X, Edit2, Save, FolderOpen, Clock, Scissors, Check, RotateCcw, Download } from 'lucide-react';

const API_URL = "http://localhost:8000";

function App() {
  const [videoFile, setVideoFile] = useState(null);
  const [excelFile, setExcelFile] = useState(null);
  
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState("idle"); 
  const [data, setData] = useState(null); 
  const [segments, setSegments] = useState([]);
  const [speakerMapping, setSpeakerMapping] = useState({}); 

  const [generating, setGenerating] = useState(false);
  const [minutes, setMinutes] = useState(null); 
  
  const [editingSlot, setEditingSlot] = useState(null); 
  const [tempSelectedName, setTempSelectedName] = useState("");

  // Estados Sesiones
  const [savedSessions, setSavedSessions] = useState([]);
  const [currentSessionName, setCurrentSessionName] = useState(""); // El nombre de la sesión cargada
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [sessionName, setSessionName] = useState(""); // El nombre en el input del modal

  // Estados Recorte (Trimming)
  const [isTrimming, setIsTrimming] = useState(false);
  const [trimStart, setTrimStart] = useState(null);
  const [trimEnd, setTrimEnd] = useState(null);
  const [trimmingProcessing, setTrimmingProcessing] = useState(false);
  const [successMsg, setSuccessMsg] = useState("");

  // Auto-cerrar mensaje de éxito
  useEffect(() => {
    if (successMsg) {
        const timer = setTimeout(() => setSuccessMsg(""), 4000);
        return () => clearTimeout(timer);
    }
  }, [successMsg]);
  const [trimName, setTrimName] = useState("");

  const videoRef = useRef(null);

  useEffect(() => {
    fetchSessions();
    if (status === "idle") {
        // Re-fetch al volver a la pantalla principal
        fetchSessions();
    }
  }, [status]);

  const fetchSessions = async () => {
    try {
      console.log("Buscando sesiones...");
      const res = await axios.get(`${API_URL}/sessions`);
      console.log("Sesiones recibidas:", res.data);
      setSavedSessions(res.data);
    } catch (err) {
      console.error("Error cargando sesiones:", err);
    }
  };

  const handleUpload = async () => {
    if (!videoFile) return;
    const formData = new FormData();
    formData.append("file", videoFile);
    if (excelFile) formData.append("attendees", excelFile);

    try {
      setStatus("uploading");
      const res = await axios.post(`${API_URL}/upload`, formData);
      setJobId(res.data.job_id);
      setStatus("processing");
    } catch (err) {
      console.error(err);
      setStatus("error");
    }
  };

  const handleGenerateMinutes = async () => {
    try {
      setGenerating(true);
      const res = await axios.post(`${API_URL}/generate-minutes/${jobId}`, {
        speaker_mapping: speakerMapping
      });
      setMinutes(res.data.minutes);
    } catch (err) {
      console.error(err);
      alert("Error generando el acta.");
    } finally {
      setGenerating(false);
    }
  };

  const handleSaveSession = async () => {
    if (!sessionName.trim()) return;
    const sessionData = {
        segments,
        speakerMapping,
        attendees: data?.attendees || [],
        video_url: data?.video_url,
        version: 1
    };
    try {
        await axios.post(`${API_URL}/sessions`, {
            name: sessionName,
            data: sessionData
        });
        setShowSaveModal(false);
        setSuccessMsg(`Sesión "${sessionName}" guardada correctamente.`);
        setCurrentSessionName(sessionName); // Actualizar el nombre actual tras guardar
        fetchSessions();
    } catch (err) {
        console.error(err);
        alert("Error al guardar la sesión.");
    }
  };

  const handleLoadSession = async (sessionName) => {
      try {
          const res = await axios.get(`${API_URL}/sessions/${sessionName}`);
          const session = res.data;
          setSegments(session.segments);
          setSpeakerMapping(session.speakerMapping || {});
          setData({
              attendees: session.attendees || [],
              video_url: session.video_url || "" 
          });
          setJobId("loaded-session"); 
          setStatus("completed");
          setCurrentSessionName(sessionName); // Guardar el nombre actual
          setSessionName(sessionName);       // Pre-rellenar el input de guardado
      } catch (err) {
          console.error(err);
          alert("Error al cargar la sesión.");
      }
  };

  // --- LÓGICA DE RECORTE ---
  const handleDownloadTranscript = () => {
    const transcriptText = segments.map(seg => {
        const time = fmtTime(seg.start);
        const name = getDisplaySpeaker(seg);
        return `[${time}] ${name}: ${seg.text}`;
    }).join("\n");

    const blob = new Blob([transcriptText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcripcion_${currentSessionName || 'reunion'}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleSetTrimPoint = (type) => {
      if (!videoRef.current) return;
      const current = videoRef.current.currentTime;
      if (type === 'start') setTrimStart(current);
      if (type === 'end') setTrimEnd(current);
  };

  const executeTrim = async () => {
      if (trimStart === null || trimEnd === null || trimEnd <= trimStart) {
          alert("Por favor define puntos de inicio y final válidos.");
          return;
      }
      
      const name = prompt("Nombre para el nuevo video recortado:", `recorte_${new Date().getTime()}`);
      if (!name) return;

      setTrimmingProcessing(true);
      try {
          // 1. Llamar al backend
          const res = await axios.post(`${API_URL}/trim-video`, {
              video_url: data.video_url,
              start: trimStart,
              end: trimEnd,
              new_name: name
          });

      // 2. Actualizar estado
      const newVideoUrl = res.data.new_video_url;
      const offset = res.data.original_start;

      // 3. Ajustar segmentos
      const newSegments = segments
        .filter(seg => seg.end > offset && seg.start < trimEnd)
        .map(seg => ({
            ...seg,
            start: Math.max(0, seg.start - offset),
            end: Math.max(0, seg.end - offset)
        }));

      setSegments(newSegments);
      setData(prev => ({ ...prev, video_url: newVideoUrl }));
      
      // No quitamos el spinner ni el modo recorte todavía, 
      // esperaremos a que el video cargue (useEffect más abajo)
      
      // Resetear UI de recorte
      setIsTrimming(false);
      setTrimStart(null);
      setTrimEnd(null);
      setSuccessMsg("Video recortado y tiempos ajustados correctamente.");

    } catch (err) {
          console.error(err);
          alert("Error al recortar el video. Revisa el backend (ffmpeg instalado?)");
      } finally {
          setTrimmingProcessing(false);
      }
  };

  useEffect(() => {
    let interval;
    if (status === "processing" && jobId) {
      interval = setInterval(async () => {
        try {
          const res = await axios.get(`${API_URL}/status/${jobId}`);
          if (res.data.status === "completed") {
            const resultData = res.data;
            setData(resultData);
            setSegments(resultData.result.segments);
            
            const initialMap = { ...resultData.result.speakers_found };
            const allSpeakers = new Set(resultData.result.segments.map(s => s.speaker));
            allSpeakers.forEach(spk => {
              if (!initialMap[spk]) initialMap[spk] = ""; 
            });
            setSpeakerMapping(initialMap);
            setStatus("completed");
            clearInterval(interval);
          } else if (res.data.status === "failed") {
            setStatus("error");
            clearInterval(interval);
          }
        } catch (err) { console.error(err); }
      }, 3000);
    }
    return () => clearInterval(interval);
  }, [status, jobId]);

  const jumpToTime = (seconds) => {
    if (videoRef.current) {
        if (typeof seconds !== 'number' || isNaN(seconds)) return;
        try {
            videoRef.current.currentTime = seconds;
            if (videoRef.current.paused) {
                videoRef.current.play().catch(e => console.warn("Autoplay:", e));
            }
        } catch (e) { console.error(e); }
    }
  };

  const applyGlobalChange = (originalId, newName) => {
    setSpeakerMapping(prev => ({ ...prev, [originalId]: newName }));
    const newSegments = segments.map(seg => {
        if (seg.manual) return seg;
        if (seg.speaker === originalId || (!seg.manual && speakerMapping[seg.speaker] === originalId)) {
             return seg; 
        }
        return seg;
    });
    setSegments(newSegments);
    setEditingSlot(null);
  };

  const applyIndividualChange = (index, newName) => {
    const newSegments = [...segments];
    newSegments[index].speaker = newName; 
    newSegments[index].manual = true; 
    setSegments(newSegments);
    setEditingSlot(null);
  };

  const getDisplaySpeaker = (segment) => {
    if (segment.manual) return segment.speaker;
    return speakerMapping[segment.speaker] || segment.speaker;
  };

  // Helper para formatear tiempo
  const fmtTime = (s) => s === null ? "--:--" : `${Math.floor(s / 60)}:${Math.floor(s % 60).toString().padStart(2, '0')}`;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-800 font-sans relative">
      <header className="bg-white border-b sticky top-0 z-20 shadow-sm">
        {successMsg && (
            <div className="absolute top-full left-0 right-0 bg-green-600 text-white text-center py-2 text-sm font-bold animate-in fade-in slide-in-from-top-2 shadow-lg">
                {successMsg}
            </div>
        )}
        <div className="max-w-5xl mx-auto px-6 py-4 flex justify-between items-center">
          <h1 className="text-xl font-bold text-blue-900 flex items-center gap-2">
            <CheckCircle className="text-blue-600" /> Gestor de Actas IBt
          </h1>
          
            <div className="flex gap-2">
              {status === "completed" && !isTrimming && (
                  <button 
                  onClick={handleDownloadTranscript}
                  className="bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-50 flex items-center gap-2"
                  title="Descargar transcripción corregida (.txt)"
                  >
                  <Download className="w-4 h-4" />
                  Descargar TXT
                  </button>
              )}

              {status === "completed" && !isTrimming && (
                  <button 
                  onClick={() => setShowSaveModal(true)}
                  className="bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-semibold hover:bg-gray-50 flex items-center gap-2"
                  title="Guardar sesión en el servidor"
                  >
                  <Save className="w-4 h-4" />
                  Guardar
                  </button>
              )}
            
            {status === "completed" && !isTrimming && (
                <button 
                onClick={handleGenerateMinutes}
                disabled={generating}
                className="bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-green-700 disabled:opacity-50 flex items-center gap-2"
                >
                {generating ? <Loader2 className="animate-spin w-4 h-4" /> : <FileText className="w-4 h-4" />}
                {generating ? "Generando..." : "Generar Acta (IA)"}
                </button>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6">
        
        {/* UPLOAD SCREEN */}
        {status === "idle" && (
          <div className="max-w-xl mx-auto mt-10 space-y-8">
            <div className="bg-white p-8 rounded-xl shadow border border-gray-100">
                <h2 className="text-lg font-semibold mb-6 text-center text-blue-900">Nueva Reunión</h2>
                <div className="space-y-4">
                <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 hover:bg-gray-50 transition-colors text-center cursor-pointer relative">
                    <input type="file" accept="video/*" onChange={e => setVideoFile(e.target.files[0])} className="absolute inset-0 opacity-0 cursor-pointer" />
                    <FileVideo className="w-10 h-10 text-blue-500 mx-auto mb-2" />
                    <span className="block font-medium text-gray-700">{videoFile ? videoFile.name : "Selecciona el video"}</span>
                </div>
                <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 hover:bg-gray-50 transition-colors text-center cursor-pointer relative">
                    <input type="file" accept=".xlsx,.xls" onChange={e => setExcelFile(e.target.files[0])} className="absolute inset-0 opacity-0 cursor-pointer" />
                    <FileSpreadsheet className="w-10 h-10 text-green-600 mx-auto mb-2" />
                    <span className="block font-medium text-gray-700">{excelFile ? excelFile.name : "Lista de asistentes (Excel)"}</span>
                </div>
                <button onClick={handleUpload} disabled={!videoFile} className="w-full bg-blue-600 text-white py-3 rounded-lg font-semibold hover:bg-blue-700 disabled:opacity-50 mt-4">
                    Comenzar Procesamiento
                </button>
                </div>
            </div>

            <div className="bg-white p-8 rounded-xl shadow border border-gray-100">
                <div className="flex justify-between items-center mb-4">
                    <h2 className="text-lg font-semibold text-gray-700 flex items-center gap-2">
                        <FolderOpen className="w-5 h-5 text-gray-500" />
                        Reanudar Sesión
                    </h2>
                    <button 
                        onClick={fetchSessions}
                        className="p-2 hover:bg-gray-100 rounded-full text-gray-400 hover:text-blue-600 transition-colors"
                        title="Actualizar lista"
                    >
                        <RotateCcw className="w-4 h-4" />
                    </button>
                </div>
                
                {savedSessions.length > 0 ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-60 overflow-y-auto pr-2">
                        {savedSessions.map((sess) => (
                            <button 
                                key={sess.name}
                                onClick={() => handleLoadSession(sess.name)}
                                className="text-left p-3 rounded-lg border border-gray-200 hover:border-blue-400 hover:bg-blue-50 transition-all group"
                            >
                                <div className="font-semibold text-gray-800 group-hover:text-blue-700 truncate">
                                    {sess.name}
                                </div>
                                <div className="text-xs text-gray-400 flex items-center gap-1 mt-1">
                                    <Clock className="w-3 h-3" />
                                    {new Date(sess.timestamp * 1000).toLocaleString()}
                                </div>
                            </button>
                        ))}
                    </div>
                ) : (
                    <div className="text-center py-4 text-gray-400 text-sm italic">
                        No hay sesiones guardadas o el servidor no responde.
                    </div>
                )}
            </div>
          </div>
        )}

        {/* LOADING SCREEN */}
        {(status === "uploading" || status === "processing") && (
            <div className="text-center py-32">
                <Loader2 className="w-16 h-16 text-blue-600 animate-spin mx-auto mb-6" />
                <h2 className="text-2xl font-bold text-gray-800">
                {status === "uploading" ? "Subiendo archivos..." : "Analizando reunión..."}
                </h2>
                <p className="text-gray-500 mt-2 max-w-md mx-auto">
                El sistema está transcribiendo el audio e identificando a los hablantes visualmente.
                </p>
            </div>
        )}

        {/* EDITOR INTERFACE */}
        {status === "completed" && data && (
          <div className="flex flex-col gap-6">
            
            {/* VIDEO PLAYER STICKY */}
            <div className="sticky top-[80px] z-10 bg-white shadow-xl rounded-xl overflow-hidden border border-gray-200">
                <div className="bg-black aspect-[21/9] lg:aspect-[24/5] relative group"> 
                    <div className="absolute inset-0 flex items-center justify-center bg-gray-900">
                        <video 
                            ref={videoRef}
                            src={`${API_URL}${data.video_url}`} 
                            controls 
                            preload="auto"
                            onLoadedData={() => {
                                // Cuando el nuevo video se carga, quitamos el procesamiento de trim
                                if (trimmingProcessing) {
                                    setTrimmingProcessing(false);
                                }
                            }}
                            className="h-full max-w-full mx-auto"
                        />
                    </div>
                </div>
                
                {/* BARRA DE HERRAMIENTAS DE VIDEO */}
                <div className="bg-gray-50 p-2 flex justify-between items-center border-t">
                    {!isTrimming ? (
                        <button 
                            onClick={() => setIsTrimming(true)}
                            className="text-gray-600 hover:text-blue-600 text-sm font-semibold flex items-center gap-2 px-3 py-1 rounded hover:bg-gray-200"
                        >
                            <Scissors className="w-4 h-4" /> Recortar Video
                        </button>
                    ) : (
                        <div className="flex items-center gap-4 w-full animate-in slide-in-from-top-2">
                            <div className="flex items-center gap-2 bg-blue-50 px-3 py-1 rounded border border-blue-100">
                                <span className="text-xs font-bold text-blue-800">MODO RECORTE</span>
                            </div>
                            
                            <div className="flex items-center gap-2">
                                <button 
                                    onClick={() => handleSetTrimPoint('start')}
                                    className="px-3 py-1 bg-white border border-gray-300 rounded text-xs font-bold hover:bg-blue-50 text-blue-700"
                                >
                                    [ Marcar Inicio: {fmtTime(trimStart)} ]
                                </button>
                                <span className="text-gray-400">➔</span>
                                <button 
                                    onClick={() => handleSetTrimPoint('end')}
                                    className="px-3 py-1 bg-white border border-gray-300 rounded text-xs font-bold hover:bg-blue-50 text-blue-700"
                                >
                                    [ Marcar Final: {fmtTime(trimEnd)} ]
                                </button>
                            </div>

                            <div className="flex-grow"></div>

                            <button 
                                onClick={() => setIsTrimming(false)}
                                className="text-gray-500 hover:text-red-500 p-1"
                                title="Cancelar"
                            >
                                <X className="w-5 h-5" />
                            </button>
                            
                            <button 
                                onClick={executeTrim}
                                disabled={trimmingProcessing || trimStart === null || trimEnd === null}
                                className="bg-red-600 text-white px-4 py-1.5 rounded text-sm font-bold hover:bg-red-700 flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {trimmingProcessing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Scissors className="w-4 h-4" />}
                                Cortar y Guardar
                            </button>
                        </div>
                    )}
                </div>
            </div>

            {/* TRANSCRIPT CARD */}
            <div className="bg-white rounded-lg shadow border border-gray-200 flex flex-col overflow-hidden min-h-[60vh]">
              <div className="p-4 border-b bg-gray-50 flex justify-between items-center">
                <h3 className="font-bold text-gray-700">Transcripción (Corrección)</h3>
                <span className="text-xs text-gray-500">{segments.length} segmentos</span>
              </div>
              
              <div className="p-8 space-y-8">
                {(segments || []).map((seg, idx) => {
                  const originalId = seg.speaker || "Desconocido"; 
                  const displayName = getDisplaySpeaker(seg); 
                  const isUnknown = !seg.manual && !speakerMapping[originalId] && 
                                    (typeof originalId === 'string' && originalId.startsWith("SPEAKER"));
                  const isEditing = editingSlot && editingSlot.index === idx;

                  return (
                    <div key={idx} className="group hover:bg-blue-50/30 p-2 -mx-2 rounded transition-colors flex gap-4 items-start relative">
                      <button 
                        onClick={() => jumpToTime(seg.start || 0)}
                        className="mt-1 flex-shrink-0 text-xs font-mono text-gray-400 hover:text-blue-600 hover:underline cursor-pointer bg-gray-100 px-2 py-1 rounded"
                      >
                        {Math.floor((seg.start || 0) / 60)}:{Math.floor((seg.start || 0) % 60).toString().padStart(2, '0')}
                      </button>

                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                            {isEditing ? (
                                <div className="absolute top-0 left-12 bg-white p-3 rounded-lg shadow-xl border border-blue-200 z-50 flex flex-col gap-2 min-w-[300px] animate-in fade-in zoom-in-95 duration-100">
                                    <div className="text-xs font-semibold text-gray-500 mb-1">
                                        Editando: <span className="text-blue-600">{displayName}</span>
                                    </div>
                                    <select 
                                        className="text-sm border-gray-300 ring-1 ring-gray-200 rounded p-2 w-full"
                                        value={tempSelectedName}
                                        onChange={(e) => setTempSelectedName(e.target.value)}
                                        onClick={(e) => e.stopPropagation()}
                                        autoFocus
                                    >
                                        <option value="">-- Seleccionar Persona --</option>
                                        {(data?.attendees || []).map((name, i) => (
                                            <option key={`${name}-${i}`} value={name}>{name || "Sin Nombre"}</option>
                                        ))}
                                        <option disabled>──────</option>
                                        <option value="Invitado">Invitado</option>
                                    </select>
                                    <div className="flex flex-col gap-1 mt-1">
                                        <button 
                                            disabled={!tempSelectedName}
                                            onClick={(e) => { e.stopPropagation(); applyGlobalChange(originalId, tempSelectedName); }}
                                            className="bg-blue-600 text-white text-xs py-2 px-3 rounded hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2"
                                        >
                                            <User className="w-3 h-3" />
                                            Aplicar a todo {originalId}
                                        </button>
                                        <button 
                                            disabled={!tempSelectedName}
                                            onClick={(e) => { e.stopPropagation(); applyIndividualChange(idx, tempSelectedName); }}
                                            className="bg-white border border-gray-300 text-gray-700 text-xs py-2 px-3 rounded hover:bg-gray-50 disabled:opacity-50 flex items-center justify-center gap-2"
                                        >
                                            <Edit2 className="w-3 h-3" />
                                            Solo esta intervención
                                        </button>
                                    </div>
                                    <button 
                                        onClick={(e) => { e.stopPropagation(); setEditingSlot(null); }}
                                        className="absolute top-2 right-2 text-gray-400 hover:text-red-500"
                                    >
                                        <X className="w-4 h-4" />
                                    </button>
                                </div>
                            ) : (
                                <button 
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        setTempSelectedName(speakerMapping[originalId] || ""); 
                                        setEditingSlot({ index: idx, id: originalId });
                                    }}
                                    className={`
                                        group/btn flex items-center gap-2 px-2 py-1 rounded transition-all border border-transparent hover:border-gray-200 hover:bg-gray-50
                                        ${isUnknown ? 'bg-orange-50 text-orange-700' : 'bg-transparent text-blue-800'}
                                    `}
                                >
                                    <span className="font-bold text-sm">{displayName}</span>
                                    <Edit2 className="w-3 h-3 text-gray-400 group-hover/btn:text-blue-600" />
                                </button>
                            )}
                        </div>
                        <p className="text-gray-700 leading-relaxed text-base">
                            {seg.text || "..."}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* MODAL GUARDAR SESION */}
        {showSaveModal && (
            <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
                <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md animate-in fade-in zoom-in-95">
                    <h3 className="text-lg font-bold text-gray-800 mb-4">Guardar Sesión</h3>
                    <p className="text-sm text-gray-600 mb-4">
                        {currentSessionName 
                            ? `Se guardará como "${currentSessionName}" (puedes cambiarlo para crear una copia).` 
                            : "Dale un nombre a esta sesión para poder recuperarla más tarde."}
                    </p>
                    
                    <input 
                        type="text" 
                        value={sessionName}
                        onChange={(e) => setSessionName(e.target.value)}
                        placeholder="Ej: Consejo Enero 2026"
                        className="w-full border border-gray-300 rounded p-2 mb-4 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                        autoFocus
                    />
                    
                    <div className="flex justify-end gap-2">
                        <button 
                            onClick={() => setShowSaveModal(false)}
                            className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded"
                        >
                            Cancelar
                        </button>
                        <button 
                            onClick={handleSaveSession}
                            disabled={!sessionName.trim()}
                            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                        >
                            Guardar
                        </button>
                    </div>
                </div>
            </div>
        )}

        {/* MODAL ACTA GENERADA */}
        {minutes && (
          <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col animate-in fade-in zoom-in-95 duration-200">
              <div className="p-4 border-b flex justify-between items-center bg-gray-50 rounded-t-xl">
                <h2 className="text-lg font-bold text-gray-800">Acta Generada</h2>
                <button onClick={() => setMinutes(null)} className="text-gray-500 hover:text-red-500">
                  <X className="w-6 h-6" />
                </button>
              </div>
              <div className="p-8 overflow-y-auto font-serif leading-loose whitespace-pre-wrap text-gray-800">
                {minutes}
              </div>
              <div className="p-4 border-t bg-gray-50 rounded-b-xl flex justify-end gap-2">
                <button onClick={() => setMinutes(null)} className="px-4 py-2 text-gray-600 hover:bg-gray-200 rounded">Cerrar</button>
                <button onClick={() => navigator.clipboard.writeText(minutes)} className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700">Copiar Texto</button>
              </div>
            </div>
          </div>
        )}

      </main>
    </div>
  )
}

export default App
