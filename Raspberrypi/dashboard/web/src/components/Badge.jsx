// Bucket-colored pill (walk/jog/run/sprint/other). Renders nothing for a null bucket.
export default function Badge({ bucket, children }) {
  if (!bucket) return null;
  return <span className={`badge ${bucket}`}>{children || bucket}</span>;
}
