export type ExtractedInfo = {
  entreprise?: string | null;
  contact?: string | null;
  urgence?: string | null;
  besoin_principal?: string | null;
};

export type ThreadSnapshot = {
  thread_id: string;
  classification: string | null;
  classification_confidence: number | null;
  extracted_info: ExtractedInfo | null;
  company_profile: string | null;
  risk_flags: string[] | null;
  knowledge_gap: boolean | null;
  draft_response: string | null;
  reasoning_log: string[] | null;
  completed_agents: string[] | null;
  action_status: string | null;
  sender: string | null;
  subject: string | null;
  pending_clarification: string | null;
  awaiting_validation: boolean;
  done: boolean;
  rejected?: boolean;
};

export type PendingThread = {
  thread_id: string;
  sender: string;
  subject: string;
  created_at: string;
};

export type HistoryRow = {
  thread_id: string;
  validated_by: string;
  classification: string;
  sender: string;
  validated_at: string;
};

export type Stats = {
  volume_by_category: { classification: string; count: number }[];
  daily_volume: { jour: string; count: number }[];
  response_times: { thread_id: string; classification: string; minutes: number }[];
  funnel_counts: Record<string, number>;
  edit_rate: { validés: number; édités: number; taux_pct: number };
  token_stats: { analyses: number; total_entree: number; total_sortie: number; moyenne_par_analyse: number };
};

export type SettingsResponse = {
  schema: Record<string, string>;
  values: Record<string, string>;
};
