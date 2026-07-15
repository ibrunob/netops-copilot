export default function CasesLoading() {
  return (
    <div className="content-frame" aria-busy="true" aria-live="polite">
      <p className="eyebrow">LOADING CASE WORKSPACE</p>
      <div className="loading-rule" />
    </div>
  );
}
