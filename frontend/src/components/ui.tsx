import type { ButtonHTMLAttributes, ReactNode } from "react";
import { errorText } from "../lib/api";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const VARIANTS: Record<Variant, string> = {
    primary: "bg-violet-600 hover:bg-violet-500 text-white",
    secondary: "bg-neutral-800 hover:bg-neutral-700 text-neutral-100 border border-neutral-700",
    danger: "bg-red-700 hover:bg-red-600 text-white",
    ghost: "hover:bg-neutral-800 text-neutral-300",
};

export function Button({
    variant = "secondary",
    className = "",
    ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
    return (
        <button
            type="button"
            {...props}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors
        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-400
        disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
        />
    );
}

export function ErrorBox({ error, children }: { error: unknown; children?: ReactNode }) {
    if (!error) return null;
    const { title, hint } = errorText(error);
    return (
        <div role="alert" className="rounded-md border border-red-800 bg-red-950/60 px-3 py-2 text-sm">
            <p className="font-medium text-red-200">{title}</p>
            {hint && <p className="mt-0.5 text-red-300/80">{hint}</p>}
            {children}
        </div>
    );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
    const c =
        tone === "warn"
            ? "border-amber-800 bg-amber-950/50 text-amber-200"
            : "border-neutral-700 bg-neutral-900 text-neutral-300";
    return <div className={`rounded-md border px-3 py-2 text-sm ${c}`}>{children}</div>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
    const tones: Record<string, string> = {
        neutral: "bg-neutral-800 text-neutral-300",
        green: "bg-emerald-900/70 text-emerald-200",
        red: "bg-red-900/60 text-red-200",
        violet: "bg-violet-900/70 text-violet-200",
        amber: "bg-amber-900/60 text-amber-200",
    };
    return (
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tones[tone] ?? tones.neutral}`}>
            {children}
        </span>
    );
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
    const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
    return (
        <div>
            <div
                role="progressbar"
                aria-valuenow={pct}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={label ?? "Progress"}
                className="h-2 w-full overflow-hidden rounded bg-neutral-800"
            >
                <div className="h-full bg-violet-500 transition-[width]" style={{ width: `${pct}%` }} />
            </div>
            {label && (
                <p className="mt-1 text-xs text-neutral-400">
                    {label} · {pct}%
                </p>
            )}
        </div>
    );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
    return (
        <label className="block text-sm">
            <span className="mb-1 block font-medium text-neutral-300">{label}</span>
            {children}
            {hint && <span className="mt-1 block text-xs text-neutral-500">{hint}</span>}
        </label>
    );
}

export const inputCls =
    "w-full rounded-md border border-neutral-700 bg-neutral-900 px-2.5 py-1.5 text-sm text-neutral-100 " +
    "focus:border-violet-500 focus:outline-none";
