type Point = { hour: string; kind: string; p95_ms: number };

/** 稀疏小时按真实时间定位；单点显示标记，不补造趋势或把空样本记为零。 */
export function RequestTrend({ points }: { points: Point[] }) {
  const rows = points
    .filter(
      (row) =>
        row.kind === 'http' &&
        Number.isFinite(row.p95_ms) &&
        row.p95_ms >= 0 &&
        Number.isFinite(Date.parse(row.hour)),
    )
    .sort((a, b) => Date.parse(a.hour) - Date.parse(b.hour));
  if (!rows.length) {
    return (
      <div className="chart-empty">
        <span className="empty-line" />
        <strong>暂无请求样本</strong>
        <p>有业务请求后，这里会显示按小时汇总的耗时。</p>
      </div>
    );
  }
  const first = Date.parse(rows[0].hour);
  const last = Date.parse(rows.at(-1)!.hour);
  const max = Math.max(1, ...rows.map((row) => row.p95_ms));
  const coords = rows.map((row) => ({
    ...row,
    x:
      first === last
        ? 324
        : 48 + ((Date.parse(row.hour) - first) / (last - first)) * 552,
    y: 142 - (row.p95_ms / max) * 110,
  }));
  const time = (value: string) =>
    new Date(value).toLocaleString(undefined, {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  return (
    <div className="request-trend">
      <div className="trend-reading">
        <strong>
          {Math.round(rows.at(-1)!.p95_ms)}
          <small>ms</small>
        </strong>
        <span>最新小时请求 P95</span>
      </div>
      <svg
        viewBox="0 0 640 180"
        role="img"
        aria-label="请求P95耗时趋势"
        className="trend-chart"
      >
        <title>请求 P95 耗时，按小时展示</title>
        <desc>
          {rows.length === 1
            ? '仅一个小时样本，无法推断变化趋势。'
            : `共${rows.length}个小时样本，时间为浏览器本地时区。`}
        </desc>
        {[0, 0.5, 1].map((ratio) => (
          <g key={ratio}>
            <line
              x1="48"
              x2="610"
              y1={142 - ratio * 110}
              y2={142 - ratio * 110}
              className="chart-grid"
            />
            <text x="37" y={146 - ratio * 110} textAnchor="end">
              {Math.round(max * ratio)}
            </text>
          </g>
        ))}
        {coords.length > 1 && (
          <>
            <polygon
              className="chart-area"
              points={`${coords[0].x},142 ${coords.map((p) => `${p.x},${p.y}`).join(' ')} ${coords.at(-1)!.x},142`}
            />
            <polyline
              className="chart-line"
              points={coords.map((p) => `${p.x},${p.y}`).join(' ')}
            />
          </>
        )}
        {coords.map((p) => (
          <circle
            key={p.hour}
            cx={p.x}
            cy={p.y}
            r={coords.length === 1 ? 6 : 3.5}
            className="chart-point"
          >
            <title>
              {time(p.hour)} · {Math.round(p.p95_ms)} ms
            </title>
          </circle>
        ))}
        <text
          x={coords.length === 1 ? 324 : 48}
          y="169"
          textAnchor={coords.length === 1 ? 'middle' : 'start'}
        >
          {time(rows[0].hour)}
        </text>
        {coords.length > 1 && (
          <text x="600" y="169" textAnchor="end">
            {time(rows.at(-1)!.hour)}
          </text>
        )}
      </svg>
      <p className="chart-caption">
        {rows.length === 1
          ? '仅有一个小时样本，暂不推断趋势。'
          : `${rows.length} 个小时样本 · 浏览器本地时区`}{' '}
        P95 不代表单次请求上限。
      </p>
    </div>
  );
}
