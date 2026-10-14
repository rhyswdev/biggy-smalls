/** Why Smalls asks how each incident was resolved: one band. */
export function KnowledgeBand() {
  return (
    <section aria-labelledby="kb-h" className="kb">
      <h2 id="kb-h" className="heading" style={{ margin: 0 }}>Why Smalls asks how each incident was resolved</h2>
      <ol className="kb-steps">
        <li><span className="kb-n">1</span><span><b>Investigate</b><span className="d">In chat, or with an agent. Both end in the same conversation.</span></span></li>
        <li><span className="kb-n">2</span><span><b>Mark it resolved</b><span className="d">Say what fixed it, and keep only the evidence that mattered.</span></span></li>
        <li><span className="kb-n">3</span><span><b>Added to the knowledge base</b><span className="d">A later incident with similar signals turns it up.</span></span></li>
      </ol>
    </section>
  );
}
