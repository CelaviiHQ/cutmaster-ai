import { useEffect, useState } from "react";

/*
 * v3-0 gate page. Routed via `?gate=tokens`.
 * Renders every new card tier + button tier and runs 5 capability checks.
 * Used as the Phase-0 go/no-go artifact inside Resolve 21's webview.
 */

interface CapCheck {
    name: string;
    ok: boolean;
    detail?: string;
}

function runCapabilityChecks(): CapCheck[] {
    const checks: CapCheck[] = [];

    // CSS custom properties
    const cssVars =
        getComputedStyle(document.documentElement)
            .getPropertyValue("--accent-blue")
            .trim() !== "";
    checks.push({ name: "CSS custom properties (--accent-blue read)", ok: cssVars });

    // :has() selector
    let hasSelector = false;
    try {
        const probe = document.createElement("div");
        probe.innerHTML = '<div id="_gate_has"><span class="_m"></span></div>';
        document.body.appendChild(probe);
        hasSelector = !!document.querySelector("#_gate_has:has(span._m)");
        document.body.removeChild(probe);
    } catch {
        hasSelector = false;
    }
    checks.push({ name: "CSS :has() selector", ok: hasSelector });

    // @container queries — feature-detect via CSS.supports
    const containerQueries =
        typeof CSS !== "undefined" && CSS.supports?.("container-type: inline-size");
    checks.push({ name: "CSS @container queries", ok: !!containerQueries });

    // AbortController on fetch
    const abortOk =
        typeof AbortController === "function" &&
        typeof new AbortController().signal?.aborted === "boolean";
    checks.push({ name: "AbortController", ok: abortOk });

    // EventSource (SSE)
    const sseOk = typeof EventSource === "function";
    checks.push({ name: "EventSource (SSE)", ok: sseOk });

    return checks;
}

export default function TokensGate() {
    const [checks, setChecks] = useState<CapCheck[]>([]);
    const [ua, setUa] = useState<string>("");

    useEffect(() => {
        setChecks(runCapabilityChecks());
        setUa(navigator.userAgent);
    }, []);

    const allPass = checks.length > 0 && checks.every((c) => c.ok);

    return (
        <div className="app">
            <div className="hdr">
                <h1>v3-0 Tokens & Tiers Gate</h1>
                <span className="sub">
                    {allPass ? (
                        <span style={{ color: "var(--ok)" }}>● All gates pass</span>
                    ) : (
                        <span style={{ color: "var(--warn)" }}>● Checking…</span>
                    )}
                </span>
            </div>

            {/* Capability checks */}
            <div className="card">
                <h2>Webview capability checks</h2>
                <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                    {checks.map((c) => (
                        <li
                            key={c.name}
                            style={{
                                padding: "6px 0",
                                borderBottom: "var(--border-thin)",
                                display: "flex",
                                justifyContent: "space-between",
                            }}
                        >
                            <span>{c.name}</span>
                            <span style={{ color: c.ok ? "var(--ok)" : "var(--err)", fontWeight: 600 }}>
                                {c.ok ? "✓" : "✗"}
                            </span>
                        </li>
                    ))}
                </ul>
                <p className="muted" style={{ marginTop: "var(--s-3)", fontSize: "var(--fs-1)" }}>
                    UA: {ua}
                </p>
            </div>

            {/* Card tiers — side by side */}
            <h2 style={{ fontSize: "var(--fs-4)", marginTop: "var(--s-5)", marginBottom: "var(--s-2)" }}>
                Card tiers
            </h2>

            <div className="card">
                <h2>Regular card (.card)</h2>
                <p>Default tier. Used for most sections. Surface-1 background, thin border.</p>
            </div>

            <div className="card card--primary">
                <h2>Primary card (.card--primary)</h2>
                <p>
                    Accent left-border, raised surface-2, larger heading. Used for the single
                    most decision-heavy pick on a screen.
                </p>
            </div>

            <details className="card card--advanced">
                <summary>Advanced card (.card--advanced) — click to expand</summary>
                <div className="card-body">
                    <p>
                        Collapsed by default. Holds tuning knobs and technical details.
                        Chevron rotates on open.
                    </p>
                    <p className="muted">Applied state summary can live in the summary row.</p>
                </div>
            </details>

            {/* Button tiers */}
            <h2 style={{ fontSize: "var(--fs-4)", marginTop: "var(--s-5)", marginBottom: "var(--s-2)" }}>
                Button tiers
            </h2>
            <div className="card">
                <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap", alignItems: "center" }}>
                    <button>Primary</button>
                    <button className="secondary">Secondary</button>
                    <button className="btn-ghost">Ghost</button>
                    <button className="btn-danger">Danger</button>
                    <button disabled>Disabled</button>
                </div>
                <p className="muted" style={{ marginTop: "var(--s-2)" }}>
                    All 32px tall, --fs-3 font-size, --radius-md.
                </p>
            </div>

            {/* Swatches */}
            <h2 style={{ fontSize: "var(--fs-4)", marginTop: "var(--s-5)", marginBottom: "var(--s-2)" }}>
                Surfaces & status
            </h2>
            <div className="card">
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "var(--s-2)" }}>
                    {["surface-0", "surface-1", "surface-2", "surface-3"].map((s) => (
                        <div
                            key={s}
                            style={{
                                background: `var(--${s})`,
                                border: "var(--border-thin)",
                                padding: "var(--s-3)",
                                borderRadius: "var(--radius-md)",
                                fontSize: "var(--fs-2)",
                            }}
                        >
                            --{s}
                        </div>
                    ))}
                </div>
                <div style={{ display: "flex", gap: "var(--s-3)", marginTop: "var(--s-3)" }}>
                    <span style={{ color: "var(--ok)" }}>● ok</span>
                    <span style={{ color: "var(--warn)" }}>● warn</span>
                    <span style={{ color: "var(--err)" }}>● err</span>
                    <span style={{ color: "var(--accent-blue)" }}>● accent</span>
                    <span style={{ color: "var(--muted)" }}>● muted</span>
                </div>
            </div>
        </div>
    );
}
