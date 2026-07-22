/**
 * Topologie du StateGraph LangGraph réel (aca/core/app.py, workflow.add_edge/add_conditional_edges)
 * — reproduite ici pour le rendu SVG du dashboard (§12 item 8). À garder synchronisée si le graphe
 * évolue, même remarque que GRAPH_EDGES côté Streamlit (ui.py).
 */
export type NodeId =
  | "ingestion"
  | "classifier"
  | "memory_lookup"
  | "risk_scan"
  | "extractor"
  | "clarification"
  | "supervisor"
  | "enrichissement"
  | "connaissance"
  | "veille"
  | "stratege"
  | "reflection"
  | "routing"
  | "notification"
  | "action";

export type GraphNode = { id: NodeId; label: string; x: number; y: number };
export type GraphEdge = { from: NodeId; to: NodeId; dashed?: boolean };

export const NODES: GraphNode[] = [
  { id: "ingestion", label: "Ingestion", x: 6, y: 14 },
  { id: "classifier", label: "Classifieur", x: 19, y: 14 },
  { id: "memory_lookup", label: "Mémoire", x: 32, y: 14 },
  { id: "risk_scan", label: "Risques", x: 45, y: 14 },
  { id: "extractor", label: "Extracteur", x: 58, y: 14 },
  { id: "clarification", label: "Clarification", x: 71, y: 14 },
  { id: "supervisor", label: "Superviseur", x: 88, y: 14 },
  { id: "enrichissement", label: "Enrichissement", x: 46, y: 48 },
  { id: "connaissance", label: "Connaissance", x: 60, y: 48 },
  { id: "veille", label: "Veille", x: 74, y: 48 },
  { id: "stratege", label: "Stratège", x: 88, y: 48 },
  { id: "reflection", label: "Réflexion", x: 88, y: 80 },
  { id: "routing", label: "Routage", x: 71, y: 80 },
  { id: "notification", label: "Notification", x: 54, y: 80 },
  { id: "action", label: "Action", x: 37, y: 80 },
];

export const EDGES: GraphEdge[] = [
  { from: "ingestion", to: "classifier" },
  { from: "classifier", to: "memory_lookup" },
  { from: "memory_lookup", to: "risk_scan" },
  { from: "risk_scan", to: "extractor" },
  { from: "extractor", to: "clarification" },
  { from: "clarification", to: "supervisor" },
  { from: "supervisor", to: "enrichissement" },
  { from: "enrichissement", to: "supervisor" },
  { from: "supervisor", to: "connaissance" },
  { from: "connaissance", to: "supervisor" },
  { from: "supervisor", to: "veille" },
  { from: "veille", to: "supervisor" },
  { from: "supervisor", to: "stratege" },
  { from: "stratege", to: "reflection" },
  { from: "reflection", to: "stratege", dashed: true },
  { from: "reflection", to: "routing" },
  { from: "routing", to: "notification" },
  { from: "notification", to: "action" },
];

export const WORKER_NODES: NodeId[] = ["enrichissement", "connaissance", "veille", "stratege"];

/** Nœuds toujours traversés une fois la pause de validation atteinte (miroir de GRAPH_FIXED_NODES, ui.py). */
export const FIXED_NODES: NodeId[] = [
  "ingestion",
  "classifier",
  "memory_lookup",
  "risk_scan",
  "extractor",
  "clarification",
  "supervisor",
  "routing",
  "notification",
];

export function completedNodes(completedAgents: string[] | null | undefined): Set<NodeId> {
  const done = new Set<NodeId>(FIXED_NODES);
  for (const agent of completedAgents ?? []) {
    if (NODES.some((n) => n.id === agent)) done.add(agent as NodeId);
  }
  if ((completedAgents ?? []).includes("stratege")) done.add("reflection");
  return done;
}
