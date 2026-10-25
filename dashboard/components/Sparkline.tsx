/** 30-day budget-remaining sparkline. Pure SVG, server-rendered; the zero line marks overspend. */
export function Sparkline({ values, width = 220, height = 40 }: { values: (number | null)[]; width?: number; height?: number }) {
  const pts = values.map((v, i) => ({ i, v })).filter((p): p is { i: number; v: number } => p.v !== null);
  if (pts.length < 2) return <p className="muted">Not enough history</p>;
  const lo = Math.min(0, ...pts.map((p) => p.v)), hi = Math.max(100, ...pts.map((p) => p.v));
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * width;
  const y = (v: number) => height - ((v - lo) / (hi - lo)) * height;
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1]!.v;
  return (
    <svg width={width} height={height} role="img" aria-label={`Budget remaining over 30 days, now ${last.toFixed(1)}%`}>
      <line x1={0} x2={width} y1={y(0)} y2={y(0)} stroke="currentColor" strokeOpacity={0.25} strokeDasharray="3 3" />
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}
