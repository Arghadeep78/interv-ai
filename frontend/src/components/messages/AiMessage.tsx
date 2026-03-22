import { Bot } from 'lucide-react';
import { useTypewriter } from '../../hooks/useTypewriter';

export function AiMessage({ content, isLatest }: { content: string; isLatest: boolean }) {
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
