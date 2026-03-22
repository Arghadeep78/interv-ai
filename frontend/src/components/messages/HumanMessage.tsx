import { User } from 'lucide-react';

export function HumanMessage({ content }: { content: string }) {
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
