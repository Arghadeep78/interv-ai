import { BarChart3 } from 'lucide-react';

export function EvalBadge({ score, topic, feedback }: { score: number; topic: string; feedback: string }) {
  const cls = score >= 7 ? 'score-high' : score >= 5 ? 'score-mid' : 'score-low';
  return (
    <div className="msg-ai glass rounded-xl px-4 py-3 max-w-[82%] space-y-1.5">
      <div className="flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-subtle" />
        <span className="text-xs text-subtle uppercase tracking-wider font-medium">{topic}</span>
        <span className={`ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${cls}`}>{score}/10</span>
      </div>
      <p className="text-muted text-sm leading-relaxed">{feedback}</p>
    </div>
  );
}
