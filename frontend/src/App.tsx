import { useState, useRef, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  Upload, FileText, Send, User, Bot,
  Loader2, CheckCircle2, Mic, BarChart3, Clock,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

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
      <div className="w-9 h-9 rounded-none bg-accent-soft border border-accent flex items-center justify-center flex-shrink-0 mb-1">
        <Bot className="w-5 h-5 text-accent-soft" />
      </div>
      <div className="glass rounded-xl rounded-bl-sm px-5 py-4 text-primary leading-relaxed text-lg font-medium">
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
      <div className="w-9 h-9 rounded-none bg-accent-alt-soft border border-accent-alt flex items-center justify-center flex-shrink-0 mb-1">
        <User className="w-5 h-5 text-accent-alt-soft" />
      </div>
      <div className="bg-accent border border-accent rounded-xl rounded-br-sm px-5 py-4 text-inverse leading-relaxed text-lg font-bold">
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
    <div className="msg-ai glass rounded-none border-l-4 border-l-accent px-5 py-4 max-w-[82%] space-y-2">
      <div className="flex items-center gap-3">
        <BarChart3 className="w-5 h-5 text-accent-soft" />
        <span className="text-sm text-accent-soft uppercase tracking-widest font-display">{topic}</span>
        <span className={`ml-auto text-sm font-display tracking-widest px-3 py-1 ${cls}`}>{score}/10</span>
      </div>
      <p className="text-secondary text-base font-bold leading-relaxed">{feedback}</p>
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
    <label className="glass upload-zone rounded-xl p-8 flex flex-col items-center gap-4 cursor-pointer border-2 border-subtle hover:border-accent">
      <div className={`w-16 h-16 rounded-xl flex items-center justify-center ${iconColor}`}>
        <Icon className="w-8 h-8" />
      </div>
      <span className="font-display text-primary text-lg tracking-wide uppercase">{label}</span>
      {file
        ? <span className="text-accent-soft text-base font-bold truncate max-w-full">{file.name}</span>
        : <span className="text-subtle text-sm font-bold tracking-widest uppercase">Select File</span>}
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
        <div className="glass rounded-none border-t-0 border-l-0 border-r-0 border-b border-subtle px-6 py-5 mb-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 bg-accent text-inverse flex items-center justify-center font-display text-xl">
              AI
            </div>
            <div>
              <h1 className="text-2xl font-display text-primary leading-tight tracking-tighter uppercase">Interv AI</h1>
              <p className="text-xs font-bold tracking-[0.2em] text-accent-soft uppercase mt-1">Autonomous Technical Interviewer</p>
            </div>
          </div>
          {status === 'interviewing' && (
            <div className="flex items-center gap-2 border border-accent bg-accent-subtle px-4 py-2">
              <div className="w-2 h-2 rounded-full bg-accent animate-pulse" />
              <Clock className="w-4 h-4 text-accent-soft" />
              <span className="text-sm font-bold font-mono text-secondary">{fmtTime(elapsedSecs)}</span>
            </div>
          )}
        </div>

        {/* Main panel */}
        <div className="glass flex-1 flex flex-col overflow-hidden border-subtle">

          {/* IDLE / UPLOADING */}
          {(status === 'idle' || status === 'uploading') && (
            <form onSubmit={handleStart} className="flex flex-col items-center justify-center h-full p-8 gap-10">
              <div className="text-center space-y-4">
                <h2 className="text-5xl md:text-6xl font-display text-primary tracking-tighter uppercase leading-none">
                  Face The<br/><span className="text-accent">Agent</span>
                </h2>
                <p className="text-muted text-lg md:text-xl font-medium max-w-lg leading-relaxed mx-auto">
                  Upload your resume and the job description to start a radically tailored technical interview.
                </p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-2xl">
                <UploadCard icon={FileText} iconColor="bg-accent-soft text-accent-soft"
                  label="Resume" accept=".pdf,.txt" file={resume}
                  onChange={e => setResume(e.target.files?.[0] ?? null)} />
                <UploadCard icon={Upload} iconColor="bg-accent-alt-soft text-accent-alt-soft"
                  label="Job Desc" accept=".pdf,.txt" file={jd}
                  onChange={e => setJd(e.target.files?.[0] ?? null)} />
              </div>
              
              <button
                type="submit"
                disabled={!resume || !jd || status === 'uploading'}
                className="mt-4 px-12 py-5 bg-accent text-inverse font-display text-xl uppercase tracking-widest hover:bg-accent-soft disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {status === 'uploading' ? (
                  <span className="flex items-center gap-3"><Loader2 className="w-6 h-6 animate-spin" /> INITIALIZING</span>
                ) : 'Launch Interview'}
              </button>
            </form>
          )}

          {/* PROCESSING */}
          {status === 'processing' && (
            <div className="flex flex-col items-center justify-center h-full gap-8">
              <div className="pulse-ring w-24 h-24 rounded-none bg-accent-soft border-2 border-accent flex items-center justify-center">
                <Loader2 className="w-10 h-10 text-accent-soft animate-spin" />
              </div>
              <div className="text-center space-y-4">
                <h2 className="text-4xl font-display text-primary tracking-tighter uppercase">Analyzing Stack</h2>
                <p className="text-muted text-lg font-medium max-w-sm leading-relaxed mt-2">
                  Building evaluation matrices. Preparing core technical questions.
                </p>
              </div>
              <div className="flex gap-2">
                {[0, 1, 2].map(i => (
                  <div key={i} className="w-2.5 h-2.5 bg-accent"
                    style={{ animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }} />
                ))}
              </div>
            </div>
          )}

          {/* READY */}
          {status === 'ready' && (
            <div className="flex flex-col items-center justify-center h-full gap-8">
              <div className="w-24 h-24 bg-accent border-4 border-accent flex items-center justify-center">
                <CheckCircle2 className="w-12 h-12 text-inverse" />
              </div>
              <div className="text-center space-y-4">
                <h2 className="text-4xl font-display text-primary tracking-tighter uppercase">Systems Ready</h2>
                <p className="text-muted text-lg font-medium max-w-sm leading-relaxed mt-2">
                  Your tailored matrix is generated. The AI awaits your command.
                </p>
              </div>
              <button onClick={beginInterview}
                className="mt-4 px-12 py-5 bg-accent text-inverse font-display text-xl uppercase tracking-widest hover:bg-accent-soft transition-all flex items-center gap-3">
                <Mic className="w-6 h-6" /> COMMENCE
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
                          <span className="text-sm text-accent-alt font-extrabold uppercase tracking-widest">
                            Question {m.qNumber}
                          </span>
                          {m.difficulty && (
                            <span className={`text-xs font-extrabold uppercase tracking-widest px-3 py-1 rounded-full ${
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
                    <div key={idx} className="space-y-4 my-6">
                      <div className="flex items-center gap-3 border-b border-accent pb-3">
                        <CheckCircle2 className="w-6 h-6 text-accent" />
                        <span className="text-xl font-display text-accent uppercase tracking-widest">EVALUATION COMPLETE</span>
                      </div>
                      {m.summary && (
                        <div className="glass rounded-none px-6 py-5 grid grid-cols-3 gap-4 border-l-4 border-l-accent text-center">
                          <div>
                            <p className="text-3xl font-display text-primary">{m.summary.total_questions}</p>
                            <p className="text-xs font-bold text-accent-soft mt-1 uppercase tracking-widest">Questions</p>
                          </div>
                          <div>
                            <p className={`text-3xl font-display ${
                              m.summary.average_score >= 7 ? 'text-accent' :
                              m.summary.average_score >= 5 ? 'text-warning' : 'text-danger'}>`
                              {m.summary.average_score}/10
                            </p>
                            <p className="text-xs font-bold text-accent-soft mt-1 uppercase tracking-widest">Avg Score</p>
                          </div>
                          <div>
                            <p className="text-3xl font-display text-primary">{m.summary.topics_covered?.length ?? 0}</p>
                            <p className="text-xs font-bold text-accent-soft mt-1 uppercase tracking-widest">Topics</p>
                          </div>
                        </div>
                      )}
                      <div className="glass rounded-none p-6 prose-dark overflow-x-auto border border-subtle-weak">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                      </div>
                    </div>
                  );
                  return null;
                })}

                {isWaiting && (
                  <div className="msg-ai flex items-end gap-3 mt-4">
                    <div className="w-9 h-9 rounded-none bg-accent-soft border border-accent flex items-center justify-center flex-shrink-0">
                      <Bot className="w-5 h-5 text-accent-soft" />
                    </div>
                    <div className="glass rounded-none px-5 py-4 flex gap-2 items-center">
                      {[0, 1, 2].map(i => (
                        <span key={i} className="w-2 h-2 bg-accent rounded-none inline-block"
                          style={{ animation: `pulse 1s ease-in-out ${i * 0.15}s infinite` }} />
                      ))}
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {status === 'interviewing' && (
                <div className="border-t-2 border-subtle p-5 bg-app-scrim backdrop-blur-md">
                  <div className="flex gap-4 items-end">
                    <textarea
                      ref={textareaRef}
                      rows={1}
                      className="flex-1 bg-surface-faint border border-subtle-strong glow-ring text-primary placeholder-faint rounded-none px-5 py-4 resize-none text-base font-medium leading-relaxed transition-all focus:bg-surface-subtle focus:border-accent"
                      placeholder={isWaiting ? 'AI IS PROCESSING…' : 'ENTER YOUR RESPONSE… (SHIFT+ENTER FOR NEWLINE)'}
                      value={currentInput}
                      onChange={handleInput}
                      onKeyDown={handleKeyDown}
                      disabled={isWaiting}
                      style={{ minHeight: '56px', maxHeight: '160px' }}
                    />
                    <button onClick={sendMessage}
                      disabled={!currentInput.trim() || isWaiting}
                      className="w-14 h-14 bg-accent hover:bg-accent-soft text-inverse disabled:opacity-30 disabled:cursor-not-allowed rounded-none flex items-center justify-center transition-all duration-200 flex-shrink-0">
                      <Send className="w-5 h-5 text-inverse ml-1" />
                    </button>
                  </div>
                  <p className="text-xs font-bold text-subtle mt-3 uppercase tracking-widest text-center">
                    Type "I want to stop" at any time to end the interview
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
