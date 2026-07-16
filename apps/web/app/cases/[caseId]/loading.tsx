import styles from "./workbench.module.css";

export default function CaseWorkbenchLoading() {
  return (
    <section aria-busy="true" aria-live="polite" className={styles.workbench}>
      <p className={styles.eyebrow}>Loading case workbench</p>
      <div className="loading-rule" />
    </section>
  );
}
