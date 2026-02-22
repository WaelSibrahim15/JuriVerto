export default function TracePanel({ showTrace, visibleTraceRows, formatTraceDetails }) {
  if (!showTrace || visibleTraceRows.length === 0) return null;

  return (
    <section className="trace-box">
      <h3>Trace</h3>
      <table>
        <thead>
          <tr>
            <th>Step</th><th>Status</th><th>Provider</th><th>Ms</th><th>Details</th>
          </tr>
        </thead>
        <tbody>
          {visibleTraceRows.map((r, idx) => (
            <tr key={`${r.step}-${idx}`}>
              <td>{r.step}</td>
              <td>{r.status}</td>
              <td>{r.provider || '-'}</td>
              <td>{r.durationMs}</td>
              <td>{formatTraceDetails(r)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}


