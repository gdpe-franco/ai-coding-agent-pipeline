export type RunStatus = "pending" | "running" | "awaiting_user" | "completed" | "failed";
export type ChatRequestStatus =
  | "received"
  | "classifying"
  | "needs_selection"
  | "enqueued"
  | "cancelled"
  | "failed";

export interface Project {
  id: string;
  title: string;
  summary: string;
  preview_id: string;
  status: string;
  created_at: string;
  updated_at: string;
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
  project_id: string | null;
  message: string;
  status: RunStatus;
  attempts_used: number;
  attempts_total: number;
  round: number;
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
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ConversationTurn {
  id: string;
  role: string;
  content: string;
  sequence: number;
  project_id: string | null;
  run_id: string | null;
  request_status: ChatRequestStatus | null;
  created_at: string;
}

export interface ProjectOption {
  id: string;
  title: string;
}

export interface PendingSelection {
  turn_id: string;
  context_version: number;
  projects: ProjectOption[];
  reason: string | null;
}

export type ChatSelectionChoice = "continue_current" | "existing_project" | "new_project";
export type FeatureMode = "continue" | "new";

export interface ChatSelectionCreate {
  context_version: number;
  choice: ChatSelectionChoice;
  feature_mode: FeatureMode;
  project_id?: string;
  title?: string;
}

export interface ChatSelectionCreated {
  run_id: string;
  project_id: string;
  context_version: number;
}

export interface Chat {
  conversation_id: string;
  context_version: number;
  selected_project_id: string | null;
  active_feature_run_id: string | null;
  pending_selection: PendingSelection | null;
  turns: ConversationTurn[];
}

export interface User {
  id: string;
  email: string;
}
