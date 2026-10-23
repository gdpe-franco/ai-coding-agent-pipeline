export type RunStatus = "pending" | "running" | "awaiting_user" | "completed" | "failed";

export interface Project {
  id: string;
  chat_id: string;
  title: string;
  summary: string;
  preview_id: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Chat {
  id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  chat_id: string;
  run_id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  sequence: number;
  run_status: RunStatus | null;
  created_at: string;
}

export interface PlanStep {
  id: string;
  name: string;
  file_path: string;
  file_type: string;
  description: string;
}

export interface Plan {
  id: string;
  project_id: string;
  summary: string;
  steps: PlanStep[];
}

export interface Issue {
  message: string;
  file_path?: string | null;
}

export interface Feedback {
  verdict: string;
  summary: string;
  issues: Issue[];
}

export interface CheckReport {
  file_structure_ok: boolean;
  js_syntax_ok: boolean;
  files_checked: number;
  syntax_checked: string[];
  issues: string[];
}

export interface Run {
  id: string;
  chat_id: string;
  project_id: string;
  execution_id: string;
  message: string;
  status: RunStatus;
  user_guidance: string | null;
  plan: Plan | null;
  latest_code: Array<{ file_path: string; content: string; checksum: string; file_type: string }>;
  feedback: Feedback | null;
  checks: CheckReport | null;
  chat_reply: string | null;
  error: string | null;
}

export interface RunEvent {
  id: string;
  sequence: number;
  execution_id: string | null;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RunArtifact {
  id: string;
  execution_id: string;
  file_path: string;
  file_type: string;
  checksum: string;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
}
