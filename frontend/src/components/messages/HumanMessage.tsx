import { User } from 'lucide-react';

export function HumanMessage({ content }: { content: string }) {
  return (
    <div className="msg-human flex items-end gap-3 max-w-[82%] ml-auto flex-row-reverse">
      <div className="w-8 h-8 rounded-full bg-accent-alt-soft border border-accent-alt flex items-center justify-center flex-shrink-0 mb-1">
        <User className="w-4 h-4 text-accent-alt" />
      </div>
      <div className="bg-accent-alt border border-accent-alt rounded-2xl rounded-br-sm px-4 py-3 text-primary leading-relaxed text-[0.93rem]">
        <p className="whitespace-pre-wrap">{content}</p>
      </div>
    </div>
  );
}
