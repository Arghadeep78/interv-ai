import { useState, useRef, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  Upload, ChevronRight, FileText, Send, User, Bot,
  Loader2, CheckCircle2, Mic, BarChart3, Clock,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Status = 'idle' | 'uploading' | 'processing' | 'ready' | 'interviewing' | 'ended';

interface AiMsg    { role: 'ai';    content: string; difficulty?: string; qNumber?: number }
interface HumanMsg { role: 'human'; content: string }
interface EvalMsg  { role: 'eval';  score: number; topic: string; feedback: string }
interface ReportMsg {
  role: 'report';
  content: string;
  summary?: { total_questions: number; average_score: number; topics_covered: string[]; topics_required: string[] };
}
type Message = AiMsg | HumanMsg | EvalMsg | ReportMsg;

// ---------------------------------------------------------------------------
// Typewriter hook — streams text char-by-char on new content
// ---------------------------------------------------------------------------
function useTypewriter(text: string, active: boolean, speed = 16) {
  const [displayed, setDisplayed] = useState('');
  const [done, setDone] = useState(false);
  const indexRef = useRef(0);
  const prevText = useRef('');

  useEffect(() => {
    if (!active) { setDisplayed(text); setDone(true); return; }
    if (text === prevText.current) return;
    prevText.current = text;
    indexRef.current = 0;
    setDisplayed('');
    setDone(false);

    const tick = () => {
      indexRef.current += 1;
      setDisplayed(text.slice(0, indexRef.current));
      if (indexRef.current < text.length) setTimeout(tick, speed);
      else setDone(true);
    };
    setTimeout(tick, speed);
  }, [text, active, speed]);

  return { displayed, done };
}

// ---------------------------------------------------------------------------
// AI bubble with typewriter
// ---------------------------------------------------------------------------
function AiMessage({ content, isLatest }: { content: string; isLatest: boolean }) {
  const { displayed, done } = useTypewriter(content, isLatest);
  const text = displayed;
  return (
    <div className="msg-ai flex items-end gap-3 max-w-[82%]">
      <div className="w-8 h-8 rounded-full bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center flex-shrink-0 mb-1">
        <Bot className="w-4 h-4 text-indigo-400" />
      </div>
      <div className="glass rounded-2xl rounded-bl-sm px-4 py-3 text-slate-200 leading-relaxed text-[0.93rem]">
        <p className={`whitespace-pre-wrap ${isLatest && !done ? 'cursor-blink' : ''}`}>{text}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Human bubble
// ---------------------------------------------------------------------------
function HumanMessage({ content }: { content: string }) {
  return (
    <div className="msg-human flex items-end gap-3 max-w-[82%] ml-auto flex-row-reverse">
      <div className="w-8 h-8 rounded-full bg-violet-500/20 border border-violet-500/30 flex items-center justify-center flex-shrink-0 mb-1">
        <User className="w-4 h-4 text-violet-400" />
      </div>
      <div className="bg-indigo-600/80 border border-indigo-500/40 rounded-2xl rounded-br-sm px-4 py-3 text-white leading-relaxed text-[0.93rem]">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Score badge
// ---------------------------------------------------------------------------
function EvalBadge({ score, topic, feedback }: { score: number; topic: string; feedback: string }) {
  const cls = score >= 7 ? 'score-high' : score >= 5 ? 'score-mid' : 'score-low';
  return (
    <div className="msg-ai glass rounded-xl px-4 py-3 max-w-[82%] space-y-1.5">
      <div className="flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-slate-400" />
        <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">{topic}</span>
        <span className={`ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${cls}`}>{score}/10</span>
      </div>
      <p className="text-slate-300 text-sm leading-relaxed">{feedback}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload card
// ---------------------------------------------------------------------------
function UploadCard({
  icon: Icon, iconColor, label, accept, file, onChange,
}: {
  icon: React.ElementType; iconColor: string; label: string;
  accept: string; file: File | null;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <label className="glass upload-zone rounded-2xl p-6 flex flex-col items-center gap-3 cursor-pointer border border-white/[0.07] hover:border-indigo-500/40">
      <div className={`w-14 h-14 rounded-2xl flex items-center justify-center ${iconColor}`}>
        <Icon className="w-7 h-7" />
      </div>
      <span className="font-semibold text-slate-200 text-sm">{label}</span>
      {file
        ? <span className="text-indigo-400 text-xs font-medium truncate max-w-full">{file.name}</span>
        : <span className="text-slate-500 text-xs">PDF or TXT</span>}
      <input type="file" accept={accept} className="sr-only" onChange={onChange} />
    </label>
  );
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const [resume, setResume]           = useState<File | null>(null);
  const [jd, setJd]                   = useState<File | null>(null);
  const [sessionId, setSessionId]     = useState<string | null>(null);
  const [status, setStatus]           = useState<Status>('idle');
  const [messages, setMessages]       = useState<Message[]>([]);
  const [currentInput, setCurrentInput] = useState('');
  const [isWaiting, setIsWaiting]     = useState(false);
  const [elapsedSecs, setElapsedSecs] = useState(0);
  const wsRef              = useRef<WebSocket | null>(null);
  const messagesEndRef     = useRef<HTMLDivElement | null>(null);
  const timerRef           = useRef<ReturnType<typeof setInterval> | null>(null);
  const latestAiIndexRef   = useRef(-1);
  const textareaRef        = useRef<HTMLTextAreaElement | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, scrollToBottom]);

  useEffect(() => {
    if (status === 'interviewing') {
      timerRef.current = setInterval(() => setElapsedSecs(s => s + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [status]);

  const fmtTime = (s: number) =>
    `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;

  // ---------- Upload ----------
  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resume || !jd) return;
    setStatus('uploading');
    const formData = new FormData();
    formData.append('resume', resume);
    formData.append('jd', jd);
    try {
      const res = await axios.post<{ session_id: string }>('http://localhost:8000/init_interview', formData);
      setSessionId(res.data.session_id);
      setStatus('processing');
      pollStatus(res.data.session_id);
    } catch {
      setStatus('idle');
    }
  };

  const pollStatus = (sid: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await axios.get<{ status: string }>(`http://localhost:8000/status/${sid}`);
        if (res.data.status === 'ready') { clearInterval(interval); setStatus('ready'); }
      } catch { /* retry */ }
    }, 2000);
  };

  // ---------- WebSocket ----------
  const beginInterview = () => {
    if (!sessionId) return;
    setStatus('interviewing');
    setElapsedSecs(0);
    const ws = new WebSocket(`ws://localhost:8000/ws/interview/${sessionId}`);

    ws.onmessage = (event: MessageEvent) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const data: any = JSON.parse(event.data as string);
      setIsWaiting(false);

      if (data.type === 'question' || data.type === 'message') {
        setMessages(prev => {
          const next: Message[] = [...prev, {
            role: 'ai', content: data.content as string,
            difficulty: data.difficulty as string | undefined,
            qNumber: data.question_number as number | undefined,
          }];
          latestAiIndexRef.current = next.length - 1;
          return next;
        });
      } else if (data.type === 'evaluation') {
        setMessages(prev => [...prev, {
          role: 'eval',
          score: data.score as number,
          topic: data.topic_tested as string,
          feedback: data.feedback as string,
        }]);
      } else if (data.type === 'status') {
        if (data.content === 'evaluating') setIsWaiting(true);
      } else if (data.type === 'report') {
        setMessages(prev => [...prev, {
          role: 'report',
          content: data.content as string,
          summary: data.summary as ReportMsg['summary'],
        }]);
        setStatus('ended');
      }
    };

    ws.onclose = () => setStatus(s => s !== 'ended' ? 'ended' : s);
    wsRef.current = ws;
  };

  // ---------- Send ----------
  const sendMessage = (e?: React.FormEvent | React.MouseEvent) => {
    e?.preventDefault();
    if (!currentInput.trim() || !wsRef.current || isWaiting) return;
    setMessages(prev => [...prev, { role: 'human', content: currentInput }]);
    wsRef.current.send(currentInput);
    setCurrentInput('');
    setIsWaiting(true);
    if (textareaRef.current) { textareaRef.current.style.height = 'auto'; }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setCurrentInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px';
  };

  // ---------------------------------------------------------------------------
  return (
    <div className="min-h-screen bg-mesh flex items-center justify-center p-4">
      <div className="w-full max-w-3xl flex flex-col" style={{ height: '96vh' }}>

        {/* Header */}
        <div className="glass rounded-2xl px-5 py-3.5 mb-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center">
              <Mic className="w-4 h-4 text-indigo-400" />
            </div>
            <div>
              <h1 className="text-sm font-semibold text-white leading-tight">Agentic AI Interviewer</h1>
              <p className="text-[11px] text-slate-500">Powered by LLaMA 3.3 · FAISS · LangGraph</p>
            </div>
          </div>
          {status === 'interviewing' && (
            <div className="flex items-center gap-2 glass rounded-full px-3 py-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              <Clock className="w-3 h-3 text-slate-400" />
              <span className="text-xs font-mono text-slate-300">{fmtTime(elapsedSecs)}</span>
            </div>
          )}
        </div>

        {/* Main panel */}
        <div className="glass rounded-2xl flex-1 flex flex-col overflow-hidden">

          {/* IDLE / UPLOADING */}
          {(status === 'idle' || status === 'uploading') && (
            <form onSubmit={handleStart} className="flex flex-col items-center justify-center h-full p-8 gap-8">
              <div className="text-center space-y-2">
                <h2 className="text-2xl font-bold text-white">Start your Interview</h2>
                <p className="text-slate-400 text-sm max-w-md">
                  Upload your resume and job description. The AI tailors a real-time
                  technical interview just for you.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-4 w-full max-w-md">
                <UploadCard icon={FileText} iconColor="bg-indigo-500/20 text-indigo-400"
                  label="Resume" accept=".pdf,.txt" file={resume}
                  onChange={e => setResume(e.target.files?.[0] ?? null)} />
                <UploadCard icon={Upload} iconColor="bg-violet-500/20 text-violet-400"
                  label="Job Description" accept=".pdf,.txt" file={jd}
                  onChange={e => setJd(e.target.files?.[0] ?? null)} />
              </div>
              <button type="submit"
                disabled={!resume || !jd || status === 'uploading'}
                className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold py-3 px-8 rounded-xl transition-all duration-200 shadow-lg shadow-indigo-500/20">
                {status === 'uploading'
                  ? <><Loader2 className="w-4 h-4 animate-spin" /> Uploading…</>
                  : <><ChevronRight className="w-4 h-4" /> Process Documents</>}
              </button>
            </form>
          )}

          {/* PROCESSING */}
          {status === 'processing' && (
            <div className="flex flex-col items-center justify-center h-full gap-6">
              <div className="pulse-ring w-20 h-20 rounded-full bg-indigo-600/20 border-2 border-indigo-500/50 flex items-center justify-center">
                <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
              </div>
              <div className="text-center space-y-1">
                <h2 className="text-xl font-semibold text-white">Analyzing Documents</h2>
                <p className="text-slate-400 text-sm max-w-xs">
                  Embedding with FAISS · Extracting topics · Preparing your interview…
                </p>
              </div>
              <div className="flex gap-1.5">
                {[0, 1, 2].map(i => (
                  <div key={i} className="w-2 h-2 rounded-full bg-indigo-500/60"
                    style={{ animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }} />
                ))}
              </div>
            </div>
          )}

          {/* READY */}
          {status === 'ready' && (
            <div className="flex flex-col items-center justify-center h-full gap-6">
              <div className="w-20 h-20 rounded-full bg-emerald-500/15 border-2 border-emerald-500/40 flex items-center justify-center">
                <CheckCircle2 className="w-9 h-9 text-emerald-400" />
              </div>
              <div className="text-center space-y-1">
                <h2 className="text-xl font-semibold text-white">Ready to Interview</h2>
                <p className="text-slate-400 text-sm max-w-xs">
                  Your customized interview is prepared. Take a breath — you've got this.
                </p>
              </div>
              <button onClick={beginInterview}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold py-3 px-8 rounded-xl transition-all duration-200 shadow-lg shadow-emerald-500/20">
                <Mic className="w-4 h-4" /> Begin Interview
              </button>
            </div>
          )}

          {/* CHAT */}
          {(status === 'interviewing' || status === 'ended') && (
            <>
              <div className="flex-1 overflow-y-auto p-5 space-y-4">
                {messages.map((m, idx) => {
                  if (m.role === 'ai') return (
                    <div key={idx}>
                      {(m.qNumber != null) && (
                        <div className="flex items-center gap-2 mb-1.5 ml-11">
                          <span className="text-xs text-indigo-400/70 font-medium uppercase tracking-wider">
                            Question {m.qNumber}
                          </span>
                          {m.difficulty && (
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                              m.difficulty === 'Hard'   ? 'score-low'  :
                              m.difficulty === 'Medium' ? 'score-mid'  : 'score-high'
                            }`}>{m.difficulty}</span>
                          )}
                        </div>
                      )}
                      <AiMessage content={m.content} isLatest={idx === latestAiIndexRef.current} />
                    </div>
                  );
                  if (m.role === 'human') return <HumanMessage key={idx} content={m.content} />;
                  if (m.role === 'eval')  return <EvalBadge key={idx} score={m.score} topic={m.topic} feedback={m.feedback} />;
                  if (m.role === 'report') return (
                    <div key={idx} className="space-y-3">
                      <div className="flex items-center gap-2 ml-1">
                        <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                        <span className="text-sm font-semibold text-emerald-400">Interview Complete — Final Report</span>
                      </div>
                      {m.summary && (
                        <div className="glass rounded-xl px-4 py-3 grid grid-cols-3 gap-3 text-center">
                          <div>
                            <p className="text-xl font-bold text-white">{m.summary.total_questions}</p>
                            <p className="text-[11px] text-slate-500 mt-0.5">Questions</p>
                          </div>
                          <div>
                            <p className={`text-xl font-bold ${
                              m.summary.average_score >= 7 ? 'text-emerald-400' :
                              m.summary.average_score >= 5 ? 'text-yellow-400' : 'text-red-400'}`}>
                              {m.summary.average_score}/10
                            </p>
                            <p className="text-[11px] text-slate-500 mt-0.5">Avg Score</p>
                          </div>
                          <div>
                            <p className="text-xl font-bold text-white">{m.summary.topics_covered?.length ?? 0}</p>
                            <p className="text-[11px] text-slate-500 mt-0.5">Topics Covered</p>
                          </div>
                        </div>
                      )}
                      <div className="glass rounded-xl p-5 prose-dark overflow-x-auto">
                        <ReactMarkdown>{m.content}</ReactMarkdown>
                      </div>
                    </div>
                  );
                  return null;
                })}

                {isWaiting && (
                  <div className="msg-ai flex items-end gap-3">
                    <div className="w-8 h-8 rounded-full bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center flex-shrink-0">
                      <Bot className="w-4 h-4 text-indigo-400" />
                    </div>
                    <div className="glass rounded-2xl rounded-bl-sm px-5 py-3 flex gap-1.5 items-center">
                      {[0, 1, 2].map(i => (
                        <span key={i} className="w-1.5 h-1.5 bg-indigo-400/60 rounded-full inline-block"
                          style={{ animation: `pulse 1s ease-in-out ${i * 0.15}s infinite` }} />
                      ))}
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {status === 'interviewing' && (
                <div className="border-t border-white/[0.06] p-4">
                  <div className="flex gap-3 items-end">
                    <textarea
                      ref={textareaRef}
                      rows={1}
                      className="flex-1 bg-white/[0.05] border border-white/10 glow-ring text-slate-200 placeholder-slate-600 rounded-xl px-4 py-3 resize-none text-sm leading-relaxed transition-all focus:bg-white/[0.07]"
                      placeholder={isWaiting ? 'AI is thinking…' : 'Type your answer… (Enter to send, Shift+Enter for newline)'}
                      value={currentInput}
                      onChange={handleInput}
                      onKeyDown={handleKeyDown}
                      disabled={isWaiting}
                      style={{ minHeight: '48px', maxHeight: '140px' }}
                    />
                    <button onClick={sendMessage}
                      disabled={!currentInput.trim() || isWaiting}
                      className="w-11 h-11 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-30 disabled:cursor-not-allowed rounded-xl flex items-center justify-center transition-all duration-200 flex-shrink-0">
                      <Send className="w-4 h-4 text-white" />
                    </button>
                  </div>
                  <p className="text-[11px] text-slate-600 mt-2 ml-1">
                    Say "I want to stop" at any time to end the interview
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
