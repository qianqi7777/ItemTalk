import { useState } from 'react';
import { Button, Drawer } from 'antd';
import {
  BookOpen,
  Brain,
  Route,
  SlidersHorizontal,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react';
import type { Citation, ProductInstance, WebSource } from '../../types/api';
import { ChatPanel } from '../../pages/ChatPanel';
import { ManualPanel } from '../manuals/ManualPanel';
import { MemoryPanel } from '../memories/MemoryPanel';
import { GuidancePanel } from '../guidance/GuidancePanel';
import { CitationCard } from '../../components/CitationCard';
import { WebSourceCard } from '../../components/WebSourceCard';

type Module = 'chat' | 'manual' | 'memory' | 'guidance';
const modules = [
  {
    id: 'manual' as const,
    label: '资料',
    description: '说明书、章节与资料来源',
    icon: BookOpen,
  },
  {
    id: 'memory' as const,
    label: '记忆',
    description: '确认并管理个人使用偏好',
    icon: Brain,
  },
  {
    id: 'guidance' as const,
    label: '操作引导',
    description: '一步一步，完成设备操作',
    icon: Route,
  },
];

export function DeviceWorkbench({
  product,
  compact,
  onChatBusyChange,
  onGuidanceBusyChange,
}: {
  product: ProductInstance;
  compact: boolean;
  onChatBusyChange: (busy: boolean) => void;
  onGuidanceBusyChange: (busy: boolean) => void;
}) {
  const [active, setActive] = useState<Module>('chat');
  const [visited, setVisited] = useState<Module[]>([]);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [mobileSourcesOpen, setMobileSourcesOpen] = useState(false);
  const [sources, setSources] = useState<{
    citations: Citation[];
    webSources: WebSource[];
  }>({ citations: [], webSources: [] });
  const select = (next: Module) => {
    setActive(next);
    setVisited((current) =>
      current.includes(next) ? current : [...current, next],
    );
    setToolsOpen(false);
  };
  const sourceList = (
    <>
      <p className="sources-intro">
        当前会话最近回答的引用。核验状态随来源显示。
      </p>
      {!sources.citations.length && !sources.webSources.length && (
        <p className="sources-empty">
          回答引用资料后，会在这里显示来源。也可以在“资料”中查看设备说明书。
        </p>
      )}
      {sources.webSources.map((source) => (
        <WebSourceCard key={source.id} source={source} />
      ))}
      {sources.citations.map((citation) => (
        <CitationCard key={citation.knowledgeChunkId} citation={citation} />
      ))}
      <Button
        onClick={() => {
          select('manual');
          setMobileSourcesOpen(false);
        }}
      >
        查看设备资料
      </Button>
    </>
  );
  return (
    <div className={`product-workbench glass-workbench module-${active}`}>
      <div className="workspace-module-bar">
        <nav aria-label="设备模块" className="workspace-modules">
          <Button
            aria-label="聊天"
            aria-pressed={active === 'chat'}
            onClick={() => select('chat')}
          >
            聊天
          </Button>
          {!compact &&
            modules.map((item) => (
              <Button
                key={item.id}
                aria-label={item.label}
                aria-pressed={active === item.id}
                onClick={() => select(item.id)}
              >
                {item.label}
              </Button>
            ))}
          {compact && active !== 'chat' && (
            <Button onClick={() => select('chat')}>返回设备聊天</Button>
          )}
        </nav>
        <div className="workspace-module-actions">
          {compact ? (
            <Button
              className="tools-toggle"
              icon={<SlidersHorizontal size={16} />}
              aria-label="说明书 · 记忆 · 操作引导"
              aria-expanded={toolsOpen}
              onClick={() => setToolsOpen(true)}
            >
              工具
            </Button>
          ) : (
            active === 'chat' && (
              <Button
                type="text"
                aria-label={sourcesOpen ? '收起资料来源' : '展开资料来源'}
                onClick={() => setSourcesOpen(!sourcesOpen)}
                icon={
                  sourcesOpen ? (
                    <PanelRightClose size={17} />
                  ) : (
                    <PanelRightOpen size={17} />
                  )
                }
              />
            )
          )}
          {compact && (
            <Button type="text" onClick={() => setMobileSourcesOpen(true)}>
              引用
            </Button>
          )}
        </div>
      </div>
      <div
        className={`workspace-module-body ${active === 'chat' && sourcesOpen && !compact ? 'has-context' : ''}`}
      >
        <div className="workspace-primary">
          <div className="workspace-chat" hidden={active !== 'chat'}>
            <ChatPanel
              product={product}
              compact={compact}
              onBusyChange={onChatBusyChange}
              onSourcesChange={setSources}
            />
          </div>
          {visited.includes('manual') && (
            <section
              className="workspace-tool-page"
              aria-label="设备资料"
              hidden={active !== 'manual'}
            >
              <ManualPanel product={product} embedded />
            </section>
          )}
          {visited.includes('memory') && (
            <section
              className="workspace-tool-page"
              aria-label="设备记忆"
              hidden={active !== 'memory'}
            >
              <MemoryPanel instanceId={product.id} embedded />
            </section>
          )}
          {visited.includes('guidance') && (
            <section
              className="workspace-tool-page"
              aria-label="设备操作引导"
              hidden={active !== 'guidance'}
            >
              <GuidancePanel
                product={product}
                onBusyChange={onGuidanceBusyChange}
                embedded
              />
            </section>
          )}
        </div>
        {!compact && active === 'chat' && sourcesOpen && (
          <aside
            className="workspace-source-panel"
            aria-label="当前回答资料来源"
          >
            <h2>资料来源</h2>
            {sourceList}
          </aside>
        )}
      </div>
      <Drawer
        title="设备工具"
        placement="bottom"
        size={360}
        open={compact && toolsOpen}
        onClose={() => setToolsOpen(false)}
        rootClassName="glass-sheet"
      >
        {modules.map(({ id, label, description, icon: Icon }) => (
          <Button
            key={id}
            className="device-tool-choice"
            aria-label={`打开${label}`}
            onClick={() => select(id)}
          >
            <Icon size={20} />
            <span>
              <strong>{label}</strong>
              <small>{description}</small>
            </span>
          </Button>
        ))}
      </Drawer>
      <Drawer
        title="资料来源"
        placement="bottom"
        size={460}
        open={compact && mobileSourcesOpen}
        onClose={() => setMobileSourcesOpen(false)}
        rootClassName="glass-sheet"
      >
        {sourceList}
      </Drawer>
    </div>
  );
}
