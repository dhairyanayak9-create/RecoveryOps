"use client";

import { useState } from "react";

export default function Home() {
  const [api, setApi] = useState("");
  async function check() {
    try {
      const r = await fetch("http://localhost:8000/health");
      const j = await r.json();
      setApi(j.status);
    } catch {
      setApi("offline");
    }
  }
  const modules = ["Finance","Insurance","CRM","ERP","Recovery","Reports"];
  return <main style={{fontFamily:"Arial",padding:40}}>
    <h1>Business Platform</h1>
    <p>Reusable foundation for business applications.</p>
    <button onClick={check}>Check API</button>
    {api && <span style={{marginLeft:12}}>API: {api}</span>}
    <section style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:16,marginTop:30}}>
      {modules.map(m=><article key={m} style={{border:"1px solid #ddd",borderRadius:12,padding:24}}><h2>{m}</h2><p>Module ready for expansion.</p></article>)}
    </section>
  </main>;
}
