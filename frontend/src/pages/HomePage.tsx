import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Input, Select, Spin, Upload } from 'antd';
import {
  Check,
  Camera,
  ChevronRight,
  FileImage,
  PackageSearch,
  ShieldCheck,
  Sparkles,
  RotateCcw,
  Upload as UploadIcon,
} from 'lucide-react';
import { api, newKey, post, upload } from '../services/api';
import type {
  Asset,
  Candidate,
  Identification,
  Job,
  ProductInstance,
} from '../types/api';
import { CandidateList } from '../features/products/CandidateList';
import { KnowledgeProgress } from '../features/products/KnowledgeProgress';
import { usePolling } from '../hooks/usePolling';
import { ChatPanel } from './ChatPanel';
import { DeviceScene } from '../components/DeviceScene';
import { ManualPanel } from '../features/manuals/ManualPanel';
import { MemoryPanel } from '../features/memories/MemoryPanel';
import { GuidancePanel } from '../features/guidance/GuidancePanel';
import { DeleteAction } from '../components/DeleteAction';

export function HomePage() {
  const [input, setInput] = useState('');
  const [asset, setAsset] = useState<Asset>();
  const [attempt, setAttempt] = useState<Identification>();
  const [product, setProduct] = useState<ProductInstance>();
  const [products, setProducts] = useState<ProductInstance[]>([]);
  const [job, setJob] = useState<Job>();
  const [busy, setBusy] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [guidanceBusy, setGuidanceBusy] = useState(false);
  const [error, setError] = useState('');
  const [manual, setManual] = useState(false);
  const [brand, setBrand] = useState('');
  const [code, setCode] = useState('');
  const [region, setRegion] = useState('');
  useEffect(() => {
    void api<{ items: ProductInstance[] }>('/me/products')
      .then((response) => {
        setProducts(response.items);
        setProduct(response.items[0]);
      })
      .catch((error) => setError(error.message));
  }, []);
  const pollAttempt = useCallback(async () => {
    if (!attempt) return;
    try {
      setAttempt(await api<Identification>(`/identifications/${attempt.id}`));
    } catch (error) {
      setError(error instanceof Error ? error.message : '查询识别结果失败');
    }
  }, [attempt]);
  usePolling(
    pollAttempt,
    !!attempt && ['queued', 'processing'].includes(attempt.status),
  );
  const pollProduct = useCallback(async () => {
    if (!product) return;
    try {
      const updated = await api<ProductInstance>(`/me/products/${product.id}`);
      setProduct(updated);
      setProducts((old) =>
        old.map((item) => (item.id === updated.id ? updated : item)),
      );
      if (updated.knowledgeJobId)
        setJob(await api<Job>(`/jobs/${updated.knowledgeJobId}`));
    } catch (error) {
      setError(error instanceof Error ? error.message : '查询资料进度失败');
    }
  }, [product]);
  usePolling(
    pollProduct,
    !!product &&
      ['queued', 'researching', 'parsing', 'embedding'].includes(
        product.knowledgeStatus,
      ),
  );

  const identify = async () => {
    setBusy(true);
    setError('');
    try {
      setAttempt(
        await post<Identification>(
          '/products/identify',
          {
            ...(input.trim() ? { inputText: input.trim() } : {}),
            ...(asset ? { assetIds: [asset.id] } : {}),
            ...(attempt ? { supersedesAttemptId: attempt.id } : {}),
          },
          newKey(),
        ),
      );
    } catch (error) {
      setError(error instanceof Error ? error.message : '识别请求失败');
    } finally {
      setBusy(false);
    }
  };
  const confirm = async (candidate?: Candidate) => {
    if (!attempt) return;
    setBusy(true);
    setError('');
    try {
      const result = await post<ProductInstance>(
        `/identifications/${attempt.id}/confirm`,
        candidate
          ? { productModelId: candidate.productModelId }
          : {
              userProvidedModel: {
                brandName: brand,
                modelCode: code,
                ...(region ? { region } : {}),
              },
            },
        newKey(),
      );
      setProduct(result);
      setProducts((old) => [
        result,
        ...old.filter((item) => item.id !== result.id),
      ]);
      setAttempt(undefined);
      setJob(undefined);
    } catch (error) {
      setError(error instanceof Error ? error.message : '型号确认失败');
    } finally {
      setBusy(false);
    }
  };
  const retryBuild = async () => {
    if (!product) return;
    setBusy(true);
    setError('');
    try {
      await post(
        `/products/${product.productModelId}/knowledge-jobs`,
        {},
        newKey(),
        product.knowledgeJobId || undefined,
      );
      await pollProduct();
    } catch (error) {
      setError(error instanceof Error ? error.message : '重试失败');
    } finally {
      setBusy(false);
    }
  };
  const restartIdentification = () => {
    setProduct(undefined);
    setAttempt(undefined);
    setAsset(undefined);
    setJob(undefined);
    setInput('');
    setManual(false);
    setError('');
  };
  return (
    <div className="workspace" id="workspace">
      <header className="page-heading">
        <div>
          <span className="eyebrow">你的设备，值得被了解</span>
          <h1>
            {product ? '用好你的每一件设备。' : '设备的答案，从这里开始。'}
          </h1>
          <p>
            {product
              ? '少一点翻找，多一点明白。和你的设备聊聊吧。'
              : '从一张照片开始，把复杂的使用说明变成简单的对话。'}
          </p>
        </div>
        {product ? (
          <Button
            className="restart-button"
            icon={<RotateCcw size={16} aria-hidden="true" />}
            onClick={restartIdentification}
            disabled={busy || chatBusy || guidanceBusy}
          >
            重新识别
          </Button>
        ) : (
          <span className="heading-note">
            <Sparkles size={17} aria-hidden="true" />
            为每一次好奇，找到答案
          </span>
        )}
      </header>
      {error && (
        <Alert
          title={error}
          type="error"
          showIcon
          closable
          onClose={() => setError('')}
          role="alert"
        />
      )}
      {product && (
        <section className="product-summary">
          <div className="product-icon">
            <PackageSearch size={32} aria-hidden="true" />
          </div>
          <div className="product-identity">
            <span className="eyebrow">当前产品</span>
            <span className="product-brand">{product.brandName}</span>
            <h2>{product.modelCode}</h2>
            <KnowledgeProgress product={product} job={job} />
          </div>
          <div className="product-actions">
            <DeleteAction
              path={`/me/products/${product.id}`}
              label="删除产品"
              explanation="该产品的会话、记忆和操作引导将删除。仍被其他资料引用的照片和公共资料会保留。"
              disabled={busy || chatBusy || guidanceBusy}
              onBusyChange={setBusy}
              onDeleted={() => {
                const remaining = products.filter(
                  (item) => item.id !== product.id,
                );
                setProducts(remaining);
                setProduct(remaining[0]);
                setAsset(undefined);
                setAttempt(undefined);
                setJob(undefined);
                setInput('');
                setError('');
              }}
            />
            {products.length > 1 && (
              <Select
                aria-label="切换产品"
                value={product.id}
                disabled={busy || chatBusy || guidanceBusy}
                options={products.map((item) => ({
                  value: item.id,
                  label: `${item.nickname || item.modelCode} · ${item.brandName}`,
                }))}
                onChange={(id) => {
                  setProduct(products.find((item) => item.id === id));
                  setJob(undefined);
                  setError('');
                }}
              />
            )}
            <span>
              <ShieldCheck size={16} aria-hidden="true" />
              {product.identityStatus === 'verified'
                ? '型号已核验'
                : '型号待来源核验'}
            </span>
            {['failed', 'partial'].includes(product.knowledgeStatus) && (
              <Button
                loading={busy}
                onClick={() =>
                  product.identityStatus === 'verified'
                    ? void retryBuild()
                    : setProduct(undefined)
                }
              >
                {product.identityStatus === 'verified'
                  ? '重试资料构建'
                  : '补充型号信息'}
              </Button>
            )}
          </div>
        </section>
      )}
      {!product && (
        <div className="recognition-layout">
          <section className="identify-card">
            <div className="section-heading">
              <span className="section-icon">
                <Camera size={20} aria-hidden="true" />
              </span>
              <div>
                <h2>认识你的产品</h2>
                <p>拍下设备，或者告诉我它的型号。</p>
              </div>
              <span className="step-counter">STEP 01</span>
            </div>
            <Upload.Dragger
              accept="image/jpeg,image/png,image/webp"
              showUploadList={false}
              disabled={busy}
              beforeUpload={(file) => {
                if (file.size > 10 * 1024 * 1024) {
                  setError('图片需小于 10 MB');
                  return false;
                }
                setBusy(true);
                setError('');
                void upload(file)
                  .then(setAsset)
                  .catch((error) => setError(error.message))
                  .finally(() => setBusy(false));
                return false;
              }}
            >
              <div className="upload-visual">
                {asset?.readUrl ? (
                  <img src={asset.readUrl} alt="已上传的产品照片" />
                ) : (
                  <Camera size={35} strokeWidth={1.5} aria-hidden="true" />
                )}
              </div>
              <h3>{asset ? '照片已上传' : '把你的设备，放进这里'}</h3>
              <p>拖入产品照片，或点击选择图片</p>
              <span className="upload-prompt">
                <UploadIcon size={15} aria-hidden="true" />
                {asset ? '更换照片' : '选择照片'}
              </span>
              <small>JPG、PNG、WebP · 最大 10 MB</small>
            </Upload.Dragger>
            {asset && (
              <DeleteAction
                path={`/assets/${asset.id}`}
                label="删除照片"
                explanation="这张私有照片将删除。如果它仍被识别、问答或操作引导引用，需要先删除关联内容。"
                disabled={busy}
                onBusyChange={setBusy}
                onDeleted={() => setAsset(undefined)}
              />
            )}
            <div className="input-divider">
              <span>也可以用文字开始</span>
            </div>
            <label className="text-label" htmlFor="product-description">
              或者，描述一下你的产品
            </label>
            <Input.TextArea
              id="product-description"
              placeholder="例如：Sony WH-1000XM5，或者铭牌上看到的品牌和型号…"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              autoSize={{ minRows: 3 }}
              maxLength={10000}
            />
            <div className="identify-footer">
              <small>
                <ShieldCheck size={14} aria-hidden="true" />
                照片保持私有，仅用于识别；将发送至模型服务处理。
              </small>
              <Button
                type="primary"
                loading={busy}
                disabled={!asset && !input.trim()}
                onClick={() => void identify()}
              >
                开始识别
                <ChevronRight size={16} />
              </Button>
            </div>
            {attempt && (
              <div className="identification-result" aria-live="polite">
                {['queued', 'processing'].includes(attempt.status) && (
                  <div>
                    <Spin size="small" /> 正在识别产品，请稍候…
                  </div>
                )}
                {attempt.status === 'failed' && (
                  <Alert
                    title={
                      attempt.clarification || '识别失败，请补充信息后重试'
                    }
                    type="error"
                    showIcon
                  />
                )}
                {attempt.status === 'waiting_for_user' && (
                  <>
                    <div className="section-heading">
                      <Check size={20} aria-hidden="true" />
                      <h2>请确认准确型号</h2>
                    </div>
                    <p>用户确认后仍需官方来源核验，才会进入公共资料库。</p>
                    {attempt.clarification && (
                      <Alert
                        title={attempt.clarification}
                        type="warning"
                        showIcon
                      />
                    )}
                    <CandidateList
                      candidates={attempt.candidates}
                      busy={busy}
                      onConfirm={(candidate) => void confirm(candidate)}
                    />
                    <Button type="link" onClick={() => setManual(!manual)}>
                      不在列表中？手动输入型号
                    </Button>
                    {manual && (
                      <div className="manual-model">
                        <Input
                          aria-label="品牌"
                          placeholder="品牌"
                          value={brand}
                          onChange={(event) => setBrand(event.target.value)}
                          maxLength={120}
                        />
                        <Input
                          aria-label="型号"
                          placeholder="准确型号"
                          value={code}
                          onChange={(event) => setCode(event.target.value)}
                          maxLength={160}
                        />
                        <Input
                          aria-label="地区"
                          placeholder="地区（可选，如 CN）"
                          value={region}
                          onChange={(event) => setRegion(event.target.value)}
                          maxLength={40}
                        />
                        <Button
                          type="primary"
                          disabled={!brand.trim() || !code.trim()}
                          loading={busy}
                          onClick={() => void confirm()}
                        >
                          确认手动型号
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </section>
          <aside className="recognition-guide">
            <span className="guide-kicker">A LITTLE CURIOSITY</span>
            <h2>
              认识它，
              <br />
              才能更好地用它。
            </h2>
            <DeviceScene />
            <div className="photo-tips">
              <FileImage size={20} aria-hidden="true" />
              <div>
                <strong>把型号拍清楚</strong>
                <p>优先拍摄底部或背面的铭牌，完整的型号信息会更有帮助。</p>
              </div>
            </div>
            <div className="photo-tips">
              <ShieldCheck size={20} aria-hidden="true" />
              <div>
                <strong>你的照片保持私有</strong>
                <p>仅用于识别，并发送至模型服务处理，不进入公共资料库。</p>
              </div>
            </div>
          </aside>
        </div>
      )}
      {product ? (
        <div key={product.id}>
          <ManualPanel product={product} />
          <MemoryPanel instanceId={product.id} />
          <GuidancePanel product={product} onBusyChange={setGuidanceBusy} />
          <ChatPanel product={product} onBusyChange={setChatBusy} />
        </div>
      ) : (
        <div className="how-it-works">
          <div>
            <FileImage size={20} aria-hidden="true" />
            <strong>01 识别产品</strong>
            <p>上传外观或铭牌</p>
          </div>
          <div>
            <Check size={20} aria-hidden="true" />
            <strong>02 确认型号</strong>
            <p>你来确认准确型号</p>
          </div>
          <div>
            <ShieldCheck size={20} aria-hidden="true" />
            <strong>03 有据问答</strong>
            <p>依据核验资料回答</p>
          </div>
        </div>
      )}
    </div>
  );
}
