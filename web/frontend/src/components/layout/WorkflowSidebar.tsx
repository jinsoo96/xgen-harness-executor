import React, { useEffect, useState } from 'react';
import { useHarnessStore } from '../hooks/useHarnessStore';

interface WorkflowItem {
    workflow_id: string;
    workflow_name: string;
    updated_at?: string;
    node_count?: number;
}

/**
 * WorkflowSidebar — 워크플로우 목록 + 대화 이력 사이드바
 */
export function WorkflowSidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
    const { workflowId, loadWorkflow, createNewWorkflow } = useHarnessStore();
    const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [tab, setTab] = useState<'workflows' | 'conversations'>('workflows');
    const [search, setSearch] = useState('');

    const loadList = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/agentflow/list/detail');
            if (res.ok) {
                const data = await res.json();
                setWorkflows(data.workflows || []);
            }
        } catch (e) {
            console.error('Failed to load workflows', e);
        }
        setLoading(false);
    };

    useEffect(() => { loadList(); }, []);

    const filtered = workflows.filter((w) =>
        !search || w.workflow_name.toLowerCase().includes(search.toLowerCase())
    );

    if (collapsed) {
        return (
            <div style={{
                width: 48, flexShrink: 0,
                background: 'var(--h-bg-secondary)',
                borderRight: '1px solid var(--h-border)',
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                paddingTop: 12, gap: 8,
            }}>
                <button onClick={onToggle} style={iconBtnStyle} title="Expand sidebar">
                    {'\u25B6'}
                </button>
                <button onClick={() => { onToggle(); createNewWorkflow(); }} style={iconBtnStyle} title="New">
                    +
                </button>
            </div>
        );
    }

    return (
        <div style={{
            width: 260, flexShrink: 0,
            background: 'var(--h-bg-secondary)',
            borderRight: '1px solid var(--h-border)',
            display: 'flex', flexDirection: 'column',
            overflow: 'hidden',
        }}>
            {/* Header */}
            <div style={{
                height: 48, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '0 14px',
                borderBottom: '1px solid var(--h-border)',
            }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--h-fg-secondary)' }}>
                    Workflows
                </span>
                <div style={{ display: 'flex', gap: 4 }}>
                    <button onClick={() => createNewWorkflow()}
                        style={{ ...iconBtnStyle, fontSize: 16 }} title="New workflow">+</button>
                    <button onClick={loadList}
                        style={iconBtnStyle} title="Refresh">{'\u21BB'}</button>
                    <button onClick={onToggle}
                        style={iconBtnStyle} title="Collapse">{'\u25C0'}</button>
                </div>
            </div>

            {/* Tab */}
            <div style={{
                display: 'flex', borderBottom: '1px solid var(--h-border)',
            }}>
                {(['workflows', 'conversations'] as const).map((t) => (
                    <button key={t} onClick={() => setTab(t)} style={{
                        flex: 1, padding: '8px 0', fontSize: 10, fontWeight: 500, cursor: 'pointer',
                        background: 'transparent', border: 'none',
                        color: tab === t ? 'var(--h-accent)' : 'var(--h-fg-subtle)',
                        borderBottom: tab === t ? '2px solid var(--h-accent)' : '2px solid transparent',
                        textTransform: 'uppercase', letterSpacing: '.1em',
                    }}>
                        {t === 'workflows' ? 'Agentflows' : 'Conversations'}
                    </button>
                ))}
            </div>

            {/* Search */}
            <div style={{ padding: '8px 10px' }}>
                <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search..."
                    style={{
                        width: '100%', padding: '6px 10px', borderRadius: 8,
                        fontSize: 11, outline: 'none',
                        background: 'var(--h-fg-ghost)', border: '1px solid var(--h-border)',
                        color: 'var(--h-fg)', boxSizing: 'border-box' as const,
                    }}
                />
            </div>

            {/* List */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 6px 8px' }}>
                {tab === 'workflows' && (
                    loading ? (
                        <div style={{ padding: 20, textAlign: 'center', fontSize: 11, color: 'var(--h-fg-faint)' }}>
                            Loading...
                        </div>
                    ) : filtered.length === 0 ? (
                        <div style={{ padding: 20, textAlign: 'center', fontSize: 11, color: 'var(--h-fg-faint)' }}>
                            {search ? 'No results' : 'No workflows yet'}
                        </div>
                    ) : (
                        filtered.map((w) => (
                            <WorkflowListItem
                                key={w.workflow_id}
                                item={w}
                                active={w.workflow_id === workflowId}
                                onClick={() => loadWorkflow(w.workflow_id, w.workflow_name)}
                            />
                        ))
                    )
                )}
                {tab === 'conversations' && (
                    <ConversationList />
                )}
            </div>
        </div>
    );
}

function WorkflowListItem({ item, active, onClick }: {
    item: WorkflowItem; active: boolean; onClick: () => void;
}) {
    const timeAgo = item.updated_at ? formatTimeAgo(item.updated_at) : '';

    return (
        <div onClick={onClick} style={{
            padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
            marginBottom: 2,
            background: active ? 'var(--h-accent-ghost)' : 'transparent',
            border: active ? '1px solid var(--h-accent-muted)' : '1px solid transparent',
        }}>
            <div style={{
                fontSize: 12, fontWeight: active ? 600 : 400,
                color: active ? 'var(--h-accent)' : 'var(--h-fg-secondary)',
                marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
                {item.workflow_name}
            </div>
            <div style={{ fontSize: 10, color: 'var(--h-fg-faint)' }}>
                {timeAgo}
            </div>
        </div>
    );
}

function ConversationList() {
    const { workflowId, interactionId, setWorkflowContext, workflowName } = useHarnessStore();
    const [conversations, setConversations] = useState<any[]>([]);

    useEffect(() => {
        if (!workflowId) return;
        (async () => {
            try {
                const res = await fetch(`/api/interaction/list?workflow_id=${workflowId}&limit=20`);
                if (res.ok) {
                    const data = await res.json();
                    setConversations(data.execution_meta_list || []);
                }
            } catch { /* ignore */ }
        })();
    }, [workflowId]);

    if (!workflowId) {
        return <div style={{ padding: 20, textAlign: 'center', fontSize: 11, color: 'var(--h-fg-faint)' }}>
            워크플로우를 먼저 선택하세요
        </div>;
    }

    return (
        <div>
            <button onClick={() => {
                const newId = `harness_${Date.now().toString(36)}`;
                setWorkflowContext(workflowId, workflowName, newId);
            }} style={{
                width: '100%', padding: '8px 12px', borderRadius: 8,
                fontSize: 11, fontWeight: 500, cursor: 'pointer',
                background: 'var(--h-accent-ghost)', border: '1px solid var(--h-accent-muted)',
                color: 'var(--h-accent)', marginBottom: 6,
            }}>
                + New Conversation
            </button>
            {conversations.map((c) => (
                <div key={c.interaction_id}
                    onClick={() => setWorkflowContext(workflowId, workflowName, c.interaction_id)}
                    style={{
                        padding: '8px 12px', borderRadius: 8, cursor: 'pointer', marginBottom: 2,
                        background: c.interaction_id === interactionId ? 'var(--h-accent-ghost)' : 'transparent',
                        border: c.interaction_id === interactionId ? '1px solid var(--h-accent-muted)' : '1px solid transparent',
                    }}>
                    <div style={{ fontSize: 11, color: 'var(--h-fg-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {c.interaction_id}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--h-fg-faint)' }}>
                        {c.interaction_count || 0} turns
                    </div>
                </div>
            ))}
        </div>
    );
}

function formatTimeAgo(dateStr: string): string {
    try {
        const diff = Date.now() - new Date(dateStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.floor(hrs / 24);
        return `${days}d ago`;
    } catch { return ''; }
}

const iconBtnStyle: React.CSSProperties = {
    width: 28, height: 28, borderRadius: 6,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'transparent', border: 'none',
    color: 'var(--h-fg-muted)', cursor: 'pointer', fontSize: 12,
};
