import { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { Upload, ChevronRight, FileText, Send, User, Bot } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

export default function App() {
  const [resume, setResume] = useState(null);
  const [jd, setJd] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [status, setStatus] = useState('idle'); // idle, uploading, processing, ready, interviewing, ended
  
  const [messages, setMessages] = useState([]);
  const [currentInput, setCurrentInput] = useState('');
  const wsRef = useRef(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleStart = async (e) => {
    e.preventDefault();
    if (!resume || !jd) {
      alert("Please upload both Resume and JD");
      return;
    }

    setStatus('uploading');
    const formData = new FormData();
    formData.append('resume', resume);
    formData.append('jd', jd);

    try {
      const res = await axios.post('http://localhost:8000/init_interview', formData);
      setSessionId(res.data.session_id);
      setStatus('processing');
      pollStatus(res.data.session_id);
    } catch (err) {
      console.error(err);
      setStatus('idle');
      alert("Error uploading documents.");
    }
  };

  const pollStatus = (sid) => {
    const interval = setInterval(async () => {
      try {
        const res = await axios.get(`http://localhost:8000/status/${sid}`);
        if (res.data.status === 'ready') {
          clearInterval(interval);
          setStatus('ready');
        }
      } catch (err) {
        console.error(err);
      }
    }, 2000);
  };

  const beginInterview = () => {
    setStatus('interviewing');
    const ws = new WebSocket(`ws://localhost:8000/ws/interview/${sessionId}`);
    
    ws.onopen = () => {
      console.log('Connected to interview');
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'question' || data.type === 'message') {
        setMessages(prev => [...prev, { role: 'ai', content: data.content }]);
      } else if (data.type === 'report') {
        setMessages(prev => [...prev, { role: 'admin', content: 'Interview Complete! Final Report Below:' }, { role: 'report', content: data.content }]);
        setStatus('ended');
      }
    };

    ws.onclose = () => {
      if(status !== 'ended') setStatus('ended');
    };

    wsRef.current = ws;
  };

  const sendMessage = (e) => {
    e.preventDefault();
    if (!currentInput.trim() || !wsRef.current) return;

    setMessages(prev => [...prev, { role: 'human', content: currentInput }]);
    wsRef.current.send(currentInput);
    setCurrentInput('');
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center p-6 font-sans">
      <div className="w-full max-w-4xl bg-white shadow-lg rounded-xl overflow-hidden flex flex-col" style={{ height: '90vh' }}>
        
        {/* Header */}
        <div className="bg-blue-600 p-6 text-white text-center">
          <h1 className="text-3xl font-bold font-sans">Agentic AI Interviewer</h1>
          <p className="opacity-80 mt-2">Upload your documents and start your real-time technical interview.</p>
        </div>

        {/* Content Area */}
        <div className="flex-1 overflow-auto p-6 relative">
          
          {(status === 'idle' || status === 'uploading') && (
            <form onSubmit={handleStart} className="flex flex-col items-center justify-center space-y-8 h-full">
              <div className="grid grid-cols-2 gap-8 w-full">
                <div className="border-2 border-dashed border-gray-300 p-8 rounded-lg text-center flex flex-col items-center hover:bg-gray-50 cursor-pointer">
                  <FileText className="w-12 h-12 text-blue-500 mb-4" />
                  <h3 className="font-semibold text-lg text-gray-800">Upload Resume</h3>
                  <input type="file" className="mt-4 text-sm w-full outline-none" onChange={(e) => setResume(e.target.files[0])} />
                </div>
                
                <div className="border-2 border-dashed border-gray-300 p-8 rounded-lg text-center flex flex-col items-center hover:bg-gray-50 cursor-pointer">
                  <Upload className="w-12 h-12 text-purple-500 mb-4" />
                  <h3 className="font-semibold text-lg text-gray-800">Upload Job Description</h3>
                  <input type="file" className="mt-4 text-sm w-full outline-none" onChange={(e) => setJd(e.target.files[0])} />
                </div>
              </div>

              <button 
                type="submit" 
                disabled={status !== 'idle'}
                className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-8 rounded-full flex items-center shadow-md transition-all disabled:opacity-50"
              >
                {status === 'idle' ? 'Process Documents' : 'Uploading...'} <ChevronRight className="ml-2" />
              </button>
            </form>
          )}

          {status === 'processing' && (
            <div className="flex flex-col items-center justify-center h-full">
              <div className="animate-spin rounded-full h-16 w-16 border-b-4 border-blue-600 mb-4"></div>
              <h2 className="text-xl font-bold text-gray-700 mt-4">Analyzing Documents...</h2>
              <p className="text-gray-500 mt-2 text-center max-w-md">Our worker is using FAISS to embed your resume and extracting skills using Groq...</p>
            </div>
          )}

          {status === 'ready' && (
            <div className="flex flex-col items-center justify-center h-full space-y-6">
              <div className="bg-green-100 p-6 rounded-full">
                <Bot className="w-16 h-16 text-green-600" />
              </div>
              <h2 className="text-2xl font-bold text-gray-800">Analysis Complete!</h2>
              <p className="text-gray-600 text-center max-w-md">The agent is ready to begin your customized interview. Good luck!</p>
              
              <button 
                onClick={beginInterview}
                className="bg-green-600 hover:bg-green-700 text-white font-bold py-3 px-10 rounded-full flex items-center shadow-lg transition-all"
              >
                Start Interview Now
              </button>
            </div>
          )}

          {(status === 'interviewing' || status === 'ended') && (
            <div className="flex flex-col space-y-4 pb-20">
              {messages.map((m, idx) => (
                <div key={idx} className={`flex ${m.role === 'human' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`flex items-end max-w-[80%] ${m.role === 'human' ? 'flex-row-reverse' : 'flex-row'}`}>
                    
                    <div className={`p-4 rounded-2xl mx-2 shadow-sm ${
                      m.role === 'human' 
                      ? 'bg-blue-600 text-white rounded-br-none' 
                      : m.role === 'admin' 
                        ? 'bg-yellow-100 text-yellow-800 font-bold w-full'
                        : m.role === 'report'
                          ? 'bg-white border-2 border-gray-200 outline-none p-6 text-gray-800 markdown-body w-full'
                          : 'bg-white text-gray-800 border rounded-bl-none'
                    }`}>
                      {m.role === 'report' ? (
                        <ReactMarkdown className="prose prose-sm prose-blue">{m.content}</ReactMarkdown>
                      ) : (
                        <p className="whitespace-pre-wrap leading-relaxed">{m.content}</p>
                      )}
                    </div>

                    {m.role !== 'report' && m.role !== 'admin' && (
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${m.role === 'human' ? 'bg-blue-200' : 'bg-gray-200'}`}>
                        {m.role === 'human' ? <User className="w-5 h-5 text-blue-700"/> : <Bot className="w-5 h-5 text-gray-700"/>}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Chat Input */}
        {status === 'interviewing' && (
          <div className="border-t bg-gray-50 p-4 shrink-0">
            <form onSubmit={sendMessage} className="flex space-x-2">
              <input
                type="text"
                className="flex-1 p-4 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-gray-700"
                placeholder="Type your answer here..."
                value={currentInput}
                onChange={e => setCurrentInput(e.target.value)}
              />
              <button 
                type="submit" 
                disabled={!currentInput.trim()}
                className="bg-blue-600 text-white rounded-xl p-4 hover:bg-blue-700 disabled:opacity-50 transition-all flex items-center justify-center min-w-[60px]"
              >
                <Send className="w-6 h-6" />
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}