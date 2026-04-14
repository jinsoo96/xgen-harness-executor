import React, { useEffect, useState } from 'react';
import { useHarnessStore } from '../hooks/useHarnessStore';

/**
 * ConfigPanel — 워크플로우 레벨 하네스 설정 드로어
 *
 * Provider/Model, System Prompt, Temperature, MCP 세션, RAG 컬렉션 등
 * 하네스 실행에 필요한 전체 설정을 관리한다.
 */
export function ConfigPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
    const {
        harnessConfig, setHarnessConfigField,
        dynamicOptions, fetchDynamicOptions,
    } = useHarnessStore();

    useEffect(() => {
        if (open) {
            fetchDynamicOptions('mcp_sessions');
            fetchDynamicOptions('rag_collections');
        }
    }, [open]);

    if (!open) return null;

    const mcpSessions = dynamicOptions['mcp_sessions'] || [];
    const ragCollections = dynamicOptions['rag_collections'] || [];

    return (
        <>
            {/* Backdrop */}
            <div onClick={onClose} style={{
                position: 'fixed', inset: 0,
                background: 'rgba(0,0,0,.5)', backdropFilter: 'blur(3px)',
                zIndex: 60,
            }} />

            {/* Panel */}
            <div style={{
                position: 'fixed', right: 0, top: 0, bottom: 0,
                width: 480, zIndex: 70,
                background: `linear-gradient(180deg, var(--h-bg-tertiary), var(--h-bg))`,
                borderLeft: '1px solid var(--h-border-strong)',
                overflowY: 'auto',
                animation: 'hSlideIn .2s ease-out',
            }}>
                {/* Header */}
                <div style={{
                    padding: '24px 28px 20px',
                    borderBottom: '1px solid var(--h-border)',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}>
                    <div>
                        <h2 style={{
                            fontSize: 18, fontWeight: 600, margin: 0,
                            fontFamily: 'var(--h-font-serif)', color: 'var(--h-fg)',
                        }}>
                            Harness Config
                        </h2>
                        <span style={{ fontSize: 11, color: 'var(--h-fg-subtle)', fontFamily: 'var(--h-font-mono)' }}>
                            워크플로우 실행 설정
                        </span>
                    </div>
                    <button onClick={onClose} style={{
                        width: 32, height: 32, borderRadius: '50%',
                        background: 'var(--h-fg-ghost)', border: '1px solid var(--h-border-strong)',
                        color: 'var(--h-fg-muted)', cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14,
                    }}>x</button>
                </div>

                <div style={{ padding: '20px 28px', display: 'flex', flexDirection: 'column', gap: 22 }}>
                    {/* Provider */}
                    <Field label="Provider">
                        <div style={{ display: 'flex', gap: 6 }}>
                            {['anthropic', 'openai', 'google'].map((p) => (
                                <button key={p}
                                    onClick={() => {
                                        setHarnessConfigField('provider', p);
                                        // 기본 모델 자동 설정
                                        if (p === 'anthropic') setHarnessConfigField('model', 'claude-sonnet-4-20250514');
                                        else if (p === 'openai') setHarnessConfigField('model', 'gpt-4o-mini');
                                        else if (p === 'google') setHarnessConfigField('model', 'gemini-2.0-flash');
                                    }}
                                    style={{
                                        flex: 1, padding: '10px 0', borderRadius: 10, fontSize: 12,
                                        fontWeight: 600, cursor: 'pointer',
                                        background: harnessConfig.provider === p ? 'var(--h-accent-subtle)' : 'var(--h-fg-ghost)',
                                        border: `1px solid ${harnessConfig.provider === p ? 'var(--h-accent-muted)' : 'var(--h-border-strong)'}`,
                                        color: harnessConfig.provider === p ? 'var(--h-accent)' : 'var(--h-fg-muted)',
                                        textTransform: 'capitalize',
                                    }}>
                                    {p}
                                </button>
                            ))}
                        </div>
                    </Field>

                    {/* Model */}
                    <Field label="Model">
                        <ModelSelect
                            provider={harnessConfig.provider}
                            value={harnessConfig.model}
                            onChange={(v) => setHarnessConfigField('model', v)}
                        />
                    </Field>

                    {/* Temperature */}
                    <Field label="Temperature">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <input type="range" min={0} max={1} step={0.1}
                                value={harnessConfig.temperature}
                                onChange={(e) => setHarnessConfigField('temperature', parseFloat(e.target.value))}
                                style={{ flex: 1, accentColor: 'var(--h-accent)' }}
                            />
                            <span style={{
                                fontSize: 14, fontWeight: 700, color: 'var(--h-accent)',
                                fontFamily: 'var(--h-font-mono)', minWidth: 32, textAlign: 'right',
                            }}>
                                {harnessConfig.temperature}
                            </span>
                        </div>
                    </Field>

                    {/* Max Tokens */}
                    <Field label="Max Tokens">
                        <input type="number" min={256} max={128000} step={256}
                            value={harnessConfig.max_tokens}
                            onChange={(e) => setHarnessConfigField('max_tokens', parseInt(e.target.value) || 8192)}
                            style={inputStyle}
                        />
                    </Field>

                    {/* System Prompt */}
                    <Field label="System Prompt">
                        <textarea
                            value={harnessConfig.system_prompt}
                            onChange={(e) => setHarnessConfigField('system_prompt', e.target.value)}
                            placeholder="시스템 프롬프트를 입력하세요..."
                            rows={6}
                            style={{
                                ...inputStyle,
                                resize: 'vertical', minHeight: 100, fontFamily: 'inherit',
                                lineHeight: 1.6,
                            }}
                        />
                    </Field>

                    {/* Divider */}
                    <div style={{ height: 1, background: 'var(--h-border)', margin: '4px 0' }} />

                    {/* MCP Sessions */}
                    <Field label="MCP Sessions" description="연결할 도구 서버 선택">
                        <ChipSelector
                            options={mcpSessions}
                            selected={harnessConfig.mcp_sessions || []}
                            onChange={(v) => setHarnessConfigField('mcp_sessions', v)}
                            emptyText="MCP 세션 없음"
                        />
                    </Field>

                    {/* RAG Collections */}
                    <Field label="RAG Collections" description="검색 대상 문서 컬렉션">
                        <ChipSelector
                            options={ragCollections}
                            selected={harnessConfig.rag_collections || []}
                            onChange={(v) => setHarnessConfigField('rag_collections', v)}
                            emptyText="컬렉션 없음"
                        />
                    </Field>

                    {/* Max Iterations */}
                    <Field label="Max Iterations" description="에이전트 루프 최대 반복 횟수">
                        <input type="number" min={1} max={50} step={1}
                            value={harnessConfig.max_iterations}
                            onChange={(e) => setHarnessConfigField('max_iterations', parseInt(e.target.value) || 10)}
                            style={{ ...inputStyle, width: 100 }}
                        />
                    </Field>
                </div>
            </div>

            <style>{`@keyframes hSlideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        </>
    );
}

// ─── Sub-components ───

function Field({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
    return (
        <div>
            <label style={{
                display: 'block', fontSize: 11, fontWeight: 600,
                color: 'var(--h-fg-subtle)', marginBottom: 6,
                textTransform: 'uppercase', letterSpacing: '.1em',
            }}>
                {label}
            </label>
            {children}
            {description && (
                <div style={{ fontSize: 10, color: 'var(--h-fg-faint)', marginTop: 4 }}>{description}</div>
            )}
        </div>
    );
}

function ModelSelect({ provider, value, onChange }: { provider: string; value: string; onChange: (v: string) => void }) {
    const models: Record<string, string[]> = {
        anthropic: ['claude-sonnet-4-20250514', 'claude-opus-4-20250514', 'claude-haiku-4-20250414'],
        openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini', 'o3-mini'],
        google: ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash'],
    };
    const list = models[provider] || [];

    return (
        <select value={value} onChange={(e) => onChange(e.target.value)} style={{ ...inputStyle, cursor: 'pointer' }}>
            {list.map((m) => (
                <option key={m} value={m} style={{ background: 'var(--h-bg-elevated)' }}>{m}</option>
            ))}
            {!list.includes(value) && value && (
                <option value={value} style={{ background: 'var(--h-bg-elevated)' }}>{value} (custom)</option>
            )}
        </select>
    );
}

function ChipSelector({ options, selected, onChange, emptyText }: {
    options: string[]; selected: string[]; onChange: (v: string[]) => void; emptyText: string;
}) {
    if (!options.length) {
        return <span style={{ fontSize: 11, color: 'var(--h-fg-faint)' }}>{emptyText}</span>;
    }

    return (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {options.map((opt) => {
                const active = selected.includes(opt);
                return (
                    <button key={opt}
                        onClick={() => onChange(active ? selected.filter((s) => s !== opt) : [...selected, opt])}
                        style={{
                            fontSize: 11, padding: '5px 14px', borderRadius: 16, cursor: 'pointer',
                            background: active ? 'var(--h-accent-subtle)' : 'var(--h-fg-ghost)',
                            border: `1px solid ${active ? 'var(--h-accent-muted)' : 'var(--h-border-strong)'}`,
                            color: active ? 'var(--h-accent)' : 'var(--h-fg-muted)',
                            fontWeight: active ? 600 : 400,
                        }}>
                        {opt}
                    </button>
                );
            })}
        </div>
    );
}

const inputStyle: React.CSSProperties = {
    width: '100%', padding: '10px 14px', borderRadius: 10, fontSize: 13, outline: 'none',
    background: 'var(--h-fg-ghost)', border: '1px solid var(--h-border-strong)',
    color: 'var(--h-fg)', fontFamily: 'inherit', boxSizing: 'border-box' as const,
};
