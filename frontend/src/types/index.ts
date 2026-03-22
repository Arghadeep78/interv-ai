export type Status = 'idle' | 'uploading' | 'processing' | 'ready' | 'interviewing' | 'ended';

export interface AiMsg { role: 'ai'; content: string; difficulty?: string; qNumber?: number }
export interface HumanMsg { role: 'human'; content: string }
export interface EvalMsg { role: 'eval'; score: number; topic: string; feedback: string }
export interface ReportMsg {
  role: 'report';
  content: string;
  summary?: { total_questions: number; average_score: number; topics_covered: string[]; topics_required: string[] };
}
export type Message = AiMsg | HumanMsg | EvalMsg | ReportMsg;
