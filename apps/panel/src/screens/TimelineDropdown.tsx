import { useEffect, useRef, useState } from "react";
import type { ProjectInfo } from "../types";

/*
 * Custom styled dropdown for the timeline picker. Replaces the native
 * <select> so the presentation is consistent across macOS / Windows /
 * Resolve's embedded webview — native selects inherit OS chrome that
 * fights the dark theme.
 */

interface Props {
    timelines: ProjectInfo["timelines"];
    value: string;
    onChange: (name: string) => void;
    placeholder?: string;
}

export default function TimelineDropdown({ timelines, value, onChange, placeholder }: Props) {
    const [open, setOpen] = useState(false);
    const rootRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        const onDocClick = (e: MouseEvent) => {
            if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") setOpen(false);
        };
        document.addEventListener("mousedown", onDocClick);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onDocClick);
            document.removeEventListener("keydown", onKey);
        };
    }, [open]);

    const active = timelines.find((t) => t.name === value);
    const label = active
        ? active.name
        : value || placeholder || "(pick a timeline)";

    return (
        <div
            ref={rootRef}
            style={{ position: "relative", width: "100%" }}
        >
            <button
                type="button"
                className="secondary"
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="listbox"
                aria-expanded={open}
                style={{
                    width: "100%",
                    justifyContent: "space-between",
                    display: "flex",
                    alignItems: "center",
                    textAlign: "left",
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--fs-2)",
                    paddingLeft: "var(--s-3)",
                    paddingRight: "var(--s-3)",
                }}
            >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {label}
                    {active?.is_current && (
                        <span
                            className="muted"
                            style={{
                                fontFamily: "var(--font-body)",
                                marginLeft: "var(--s-2)",
                                fontSize: "var(--fs-2)",
                                color: "var(--accent-blue)",
                            }}
                        >
                            · currently open
                        </span>
                    )}
                    {active?.item_count ? (
                        <span
                            className="muted"
                            style={{
                                fontFamily: "var(--font-body)",
                                marginLeft: "var(--s-2)",
                                fontSize: "var(--fs-2)",
                            }}
                        >
                            · {active.item_count} item
                            {active.item_count === 1 ? "" : "s"}
                        </span>
                    ) : null}
                </span>
                <span
                    style={{
                        color: "var(--text-tertiary)",
                        transform: open ? "rotate(180deg)" : "none",
                        transition: "transform 120ms",
                        fontSize: "var(--fs-2)",
                        marginLeft: "var(--s-2)",
                    }}
                >
                    ▾
                </span>
            </button>
            {open && (
                <ul
                    role="listbox"
                    style={{
                        position: "absolute",
                        top: "calc(100% + 4px)",
                        left: 0,
                        right: 0,
                        background: "var(--surface-2)",
                        border: "var(--border-thin)",
                        borderRadius: "var(--radius-md)",
                        padding: "var(--s-1) 0",
                        margin: 0,
                        listStyle: "none",
                        maxHeight: 320,
                        overflowY: "auto",
                        zIndex: 20,
                        boxShadow: "0 8px 24px rgba(0, 0, 0, 0.4)",
                    }}
                >
                    {timelines.length === 0 && (
                        <li
                            style={{
                                padding: "var(--s-2) var(--s-3)",
                                color: "var(--text-tertiary)",
                                fontSize: "var(--fs-2)",
                            }}
                        >
                            No timelines in project
                        </li>
                    )}
                    {timelines.map((t) => {
                        const selected = t.name === value;
                        return (
                            <li
                                key={t.name}
                                role="option"
                                aria-selected={selected}
                                onClick={() => {
                                    onChange(t.name);
                                    setOpen(false);
                                }}
                                style={{
                                    padding: "var(--s-2) var(--s-3)",
                                    cursor: "pointer",
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "space-between",
                                    gap: "var(--s-2)",
                                    background: selected ? "var(--accent-blue-tint)" : "transparent",
                                    color: selected ? "var(--accent-blue)" : "var(--text-primary)",
                                    fontSize: "var(--fs-2)",
                                }}
                                onMouseEnter={(e) => {
                                    if (!selected) {
                                        (e.currentTarget as HTMLLIElement).style.background =
                                            "var(--surface-3)";
                                    }
                                }}
                                onMouseLeave={(e) => {
                                    if (!selected) {
                                        (e.currentTarget as HTMLLIElement).style.background = "transparent";
                                    }
                                }}
                            >
                                <span
                                    style={{
                                        fontFamily: "var(--font-mono)",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        whiteSpace: "nowrap",
                                        flex: 1,
                                    }}
                                >
                                    {t.name}
                                </span>
                                <span
                                    className="muted"
                                    style={{
                                        fontSize: "var(--fs-1)",
                                        whiteSpace: "nowrap",
                                        fontFamily: "var(--font-body)",
                                    }}
                                >
                                    {t.is_current && (
                                        <span style={{ color: "var(--accent-blue)", marginRight: "var(--s-2)" }}>
                                            ● open
                                        </span>
                                    )}
                                    {t.item_count ? `${t.item_count} item${t.item_count === 1 ? "" : "s"}` : ""}
                                </span>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}
