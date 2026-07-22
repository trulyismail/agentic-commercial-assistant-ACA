"use client";

import { useMemo } from "react";
import { motion } from "motion/react";
import { NODES, EDGES, type NodeId } from "@/lib/graph-topology";

type Props = {
  /** "ambient" = fond décoratif (login) : pulses en boucle continue, aucun état réel. */
  mode: "ambient" | "progress";
  activeNode?: NodeId | null;
  doneNodes?: Set<NodeId>;
  className?: string;
};

function edgePath(from: { x: number; y: number }, to: { x: number; y: number }) {
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  // Léger arc perpendiculaire au segment — les connexions superviseur <-> worker se chevauchent
  // moins qu'avec des lignes droites, et ça donne au graphe une allure de circuit plutôt que
  // d'organigramme plat.
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const bend = 6;
  const nx = -dy;
  const ny = dx;
  const len = Math.hypot(nx, ny) || 1;
  const cx = mx + (nx / len) * bend;
  const cy = my + (ny / len) * bend;
  return `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`;
}

export function PulseGraph({ mode, activeNode, doneNodes, className }: Props) {
  const nodeById = useMemo(() => new Map(NODES.map((n) => [n.id, n])), []);

  return (
    <svg
      viewBox="0 0 100 100"
      className={className}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Graphe de l'équipe d'agents ACA"
    >
      <defs>
        <filter id="pg-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="1.6" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {EDGES.map((e, i) => {
        const from = nodeById.get(e.from)!;
        const to = nodeById.get(e.to)!;
        const isDone = mode === "progress" && doneNodes?.has(e.from) && doneNodes?.has(e.to);
        return (
          <path
            key={`${e.from}-${e.to}-${i}`}
            d={edgePath(from, to)}
            fill="none"
            stroke={isDone ? "var(--teal-400)" : "var(--line)"}
            strokeWidth={isDone ? 0.5 : 0.35}
            strokeDasharray={e.dashed ? "1.5 1.2" : undefined}
            opacity={isDone ? 0.8 : 0.55}
          />
        );
      })}

      {mode === "ambient" &&
        EDGES.filter((e) => !e.dashed).map((e, i) => {
          const from = nodeById.get(e.from)!;
          const to = nodeById.get(e.to)!;
          return (
            <circle key={`pulse-${i}`} r="0.9" fill="var(--teal-300)" filter="url(#pg-glow)">
              <animateMotion
                dur={`${3.5 + (i % 5) * 0.6}s`}
                begin={`${i * 0.35}s`}
                repeatCount="indefinite"
                path={edgePath(from, to)}
              />
              <animate
                attributeName="opacity"
                values="0;0.9;0"
                dur={`${3.5 + (i % 5) * 0.6}s`}
                begin={`${i * 0.35}s`}
                repeatCount="indefinite"
              />
            </circle>
          );
        })}

      {NODES.map((n, i) => {
        const done = mode === "progress" && doneNodes?.has(n.id);
        const active = mode === "progress" && activeNode === n.id;
        const fill = active ? "var(--amber-400)" : done ? "var(--teal-400)" : "var(--ink-800)";
        const stroke = active ? "var(--amber-300)" : done ? "var(--teal-300)" : "var(--muted-dim)";
        return (
          <g key={n.id}>
            <motion.circle
              cx={n.x}
              cy={n.y}
              r={active ? 2.6 : 2.1}
              fill={fill}
              stroke={stroke}
              strokeWidth={0.4}
              filter={active || (mode === "ambient" && i % 4 === 0) ? "url(#pg-glow)" : undefined}
              animate={
                mode === "ambient"
                  ? { opacity: [0.5, 1, 0.5] }
                  : active
                    ? { scale: [1, 1.15, 1] }
                    : {}
              }
              transition={
                mode === "ambient"
                  ? { duration: 2.6, repeat: Infinity, delay: (i * 0.18) % 2.6, ease: "easeInOut" }
                  : { duration: 1.4, repeat: Infinity, ease: "easeInOut" }
              }
            />
            <text
              x={n.x}
              y={n.y + 4.6}
              textAnchor="middle"
              fontSize="2.4"
              fontFamily="var(--font-mono)"
              fill={active ? "var(--amber-300)" : done ? "var(--paper-dim)" : "var(--muted)"}
              opacity={mode === "progress" ? 1 : 0.85}
            >
              {n.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
