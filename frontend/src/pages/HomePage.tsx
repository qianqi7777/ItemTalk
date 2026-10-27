import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select } from 'antd';
import {
  Camera,
  Check,
  ChevronRight,
  FileImage,
  PackageSearch,
  ShieldCheck,
  Sparkles,
  RotateCcw,
} from 'lucide-react';
import { api, newKey, post } from '../services/api';
import type {
  Asset,
  Candidate,
  Identification,
  Job,
  ProductInstance,
} from '../types/api';
import { RecognitionPhotos } from '../features/products/RecognitionPhotos';
import { RecognitionResult } from '../features/products/RecognitionResult';
import { useSession } from '../stores/session';
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
  const [photos, setPhotos] = useState<Asset[]>([]);
  const [discardedPhotos, setDiscardedPhotos] = useState<string[]>([]);
  const [rejectedCandidates, setRejectedCandidates] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [recognitionEpoch, setRecognitionEpoch] = useState(0);
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
  const selectionRevision = useRef(0);
  const recognitionRevision = useRef(0);
  const attemptRef = useRef<Identification | undefined>(undefined);
  const { user } = useSession();
  const storageKey = user ? `itemtalk.recognition:${user.id}` : null;
  const rejectionKey = storageKey ? `${storageKey}:rejected` : null;
  const rememberRejections = useCallback(
    (attemptId?: string, ids: string[] = []) => {
      if (!rejectionKey) return;
      try {
        if (attemptId && ids.length)
          sessionStorage.setItem(
            rejectionKey,
            JSON.stringify({ attemptId, ids }),
          );
        else sessionStorage.removeItem(rejectionKey);
      } catch {
        /* Rejection stays in memory if browser storage is unavailable. */
      }
    },
    [rejectionKey],
  );
  const restoreRejections = useCallback(
    (restored: Identification) => {
      if (!rejectionKey) return [];
      try {
        const saved = JSON.parse(
          sessionStorage.getItem(rejectionKey) ?? 'null',
        ) as { attemptId?: unknown; ids?: unknown } | null;
        if (saved?.attemptId !== restored.id || !Array.isArray(saved.ids))
          return [];
        const allowed = new Set(
          restored.candidates.flatMap((candidate) => {
            const id = candidate.candidateId ?? candidate.productModelId;
            return id ? [id] : [];
          }),
        );
        return [
          ...new Set(
            saved.ids.filter(
              (id): id is string => typeof id === 'string' && allowed.has(id),
            ),
          ),
        ].slice(0, 5);
      } catch {
        return [];
      }
    },
    [rejectionKey],
  );
  const rememberAttempt = useCallback(
    (value?: Identification) => {
      attemptRef.current = value;
      setAttempt(value);
      if (storageKey) {
        try {
          if (value) sessionStorage.setItem(storageKey, value.id);
          else sessionStorage.removeItem(storageKey);
        } catch {
          /* The server retains the private round even when browser storage is unavailable. */
        }
      }
    },
    [storageKey],
  );
  const selectProduct = useCallback((value?: ProductInstance) => {
    // 切换、删除或重新识别即使回到同一产品，也不能接受此前页面的迟到轮询。
    selectionRevision.current += 1;
    setProduct(value);
  }, []);
  useEffect(() => {
    let active = true;
    const revision = selectionRevision.current;
    const recognition = recognitionRevision.current;
    void api<{ items: ProductInstance[] }>('/me/products')
      .then(async (response) => {
        if (
          !active ||
          revision !== selectionRevision.current ||
          recognition !== recognitionRevision.current
        )
          return;
        setProducts(response.items);
        const saved = storageKey ? sessionStorage.getItem(storageKey) : null;
        if (saved && /^[a-f0-9-]{36}$/i.test(saved)) {
          try {
            let restored = await api<Identification>(
              `/identifications/${saved}`,
            );
            for (let index = 0; restored.supersededBy && index < 4; index++) {
              restored = await api<Identification>(
                `/identifications/${restored.supersededBy}`,
              );
            }
            if (
              !active ||
              revision !== selectionRevision.current ||
              recognition !== recognitionRevision.current
            )
              return;
            if (restored.status !== 'confirmed') {
              selectProduct(undefined);
              rememberAttempt(restored);
              setPhotos(restored.photos ?? []);
              setRejectedCandidates(restoreRejections(restored));
              return;
            }
          } catch {
            /* Expired or deleted drafts must not prevent opening the device workspace. */
          }
          if (
            !active ||
            revision !== selectionRevision.current ||
            recognition !== recognitionRevision.current
          )
            return;
          if (storageKey) sessionStorage.removeItem(storageKey);
          rememberRejections();
        }
        selectProduct(response.items[0]);
      })
      .catch((error) => {
        if (
          active &&
          revision === selectionRevision.current &&
          recognition === recognitionRevision.current
        )
          setError(error.message);
      });
    return () => {
      active = false;
      selectionRevision.current += 1;
    };
  }, [
    selectProduct,
    storageKey,
    rememberAttempt,
    restoreRejections,
    rememberRejections,
  ]);
  const pollAttempt = useCallback(async () => {
    if (!attempt) return;
    const revision = recognitionRevision.current;
    try {
      const updated = await api<Identification>(
        `/identifications/${attempt.id}`,
      );
      if (
        revision === recognitionRevision.current &&
        attemptRef.current?.id === attempt.id
      )
        rememberAttempt(updated);
    } catch (error) {
      if (revision === recognitionRevision.current)
        setError(error instanceof Error ? error.message : '查询识别结果失败');
    }
  }, [attempt, rememberAttempt]);
  usePolling(
    pollAttempt,
    !!attempt && ['queued', 'processing'].includes(attempt.status),
  );
  const pollProduct = useCallback(async () => {
    if (!product) return;
    const revision = selectionRevision.current;
    try {
      const updated = await api<ProductInstance>(`/me/products/${product.id}`);
      if (revision !== selectionRevision.current) return;
      setProduct(updated);
      setProducts((old) =>
        old.map((item) => (item.id === updated.id ? updated : item)),
      );
      if (updated.knowledgeJobId) {
        const updatedJob = await api<Job>(`/jobs/${updated.knowledgeJobId}`);
        if (revision === selectionRevision.current) setJob(updatedJob);
      }
    } catch (error) {
      if (revision === selectionRevision.current)
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

  const identify = async (
    rejectedCandidateIds: string[] = rejectedCandidates,
  ) => {
    const revision = ++recognitionRevision.current;
    setBusy(true);
    setError('');
    try {
      const updated = await post<Identification>(
        '/products/identify',
        {
          ...(input.trim() ? { inputText: input.trim() } : {}),
          ...(photos.length
            ? { assetIds: photos.map((photo) => photo.id) }
            : {}),
          ...(attempt ? { supersedesAttemptId: attempt.id } : {}),
          ...(discardedPhotos.length
            ? { discardAssetIds: discardedPhotos }
            : {}),
          ...(rejectedCandidateIds.length ? { rejectedCandidateIds } : {}),
        },
        newKey(),
      );
      if (revision !== recognitionRevision.current) return;
      rememberAttempt(updated);
      setPhotos(updated.photos ?? photos);
      setDiscardedPhotos([]);
      setRejectedCandidates([]);
      rememberRejections();
      setInput('');
    } catch (error) {
      if (revision === recognitionRevision.current)
        setError(error instanceof Error ? error.message : '识别请求失败');
    } finally {
      if (revision === recognitionRevision.current) setBusy(false);
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
          ? candidate.candidateId
            ? { candidateId: candidate.candidateId }
            : { productModelId: candidate.productModelId }
          : {
              userProvidedModel: {
                brandName: brand,
                modelCode: code,
                ...(region ? { region } : {}),
              },
            },
        newKey(),
      );
      selectProduct(result);
      setProducts((old) => [
        result,
        ...old.filter((item) => item.id !== result.id),
      ]);
      recognitionRevision.current++;
      rememberAttempt(undefined);
      setPhotos([]);
      setDiscardedPhotos([]);
      setRejectedCandidates([]);
      rememberRejections();
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
    selectProduct(undefined);
    recognitionRevision.current++;
    rememberAttempt(undefined);
    setRecognitionEpoch((value) => value + 1);
    setPhotos([]);
    setDiscardedPhotos([]);
    setRejectedCandidates([]);
    rememberRejections();
    setUploading(false);
    setBusy(false);
    setJob(undefined);
    setInput('');
    setManual(false);
    setError('');
  };
  return (
    <main className="workspace" id="workspace">
      <header className="page-heading">
        <div>
          <span className="eyebrow">设备工作区 / DEVICE WORKSPACE</span>
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
      {!product && products.length > 0 && (
        <section className="device-library" aria-label="已有设备">
          <div className="library-heading">
            <h2>继续了解你的设备</h2>
            <span>{products.length} 件设备</span>
          </div>
          <div className="device-library-items">
            {products.map((item) => (
              <button
                type="button"
                key={item.id}
                disabled={busy || chatBusy || guidanceBusy}
                onClick={() => {
                  selectProduct(item);
                  setJob(undefined);
                  setError('');
                }}
              >
                <PackageSearch size={21} aria-hidden="true" />
                <span>
                  <small>{item.brandName}</small>
                  <strong>{item.nickname || item.modelCode}</strong>
                </span>
                <ChevronRight size={17} aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
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
                selectProduct(remaining[0]);
                setPhotos([]);
                setDiscardedPhotos([]);
                setRejectedCandidates([]);
                rememberRejections();
                rememberAttempt(undefined);
                recognitionRevision.current++;
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
                  selectProduct(products.find((item) => item.id === id));
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
                    : selectProduct(undefined)
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
            <RecognitionPhotos
              key={recognitionEpoch}
              photos={photos}
              disabled={
                busy ||
                (!!attempt &&
                  ['queued', 'processing'].includes(attempt.status)) ||
                (attempt?.roundNo ?? 0) >= (attempt?.maxRounds ?? 4)
              }
              onUploaded={(photo) =>
                setPhotos((old) =>
                  old.some((value) => value.id === photo.id)
                    ? old
                    : [...old, photo].slice(0, 10),
                )
              }
              onRemove={(id) => {
                setPhotos((old) => old.filter((value) => value.id !== id));
                if (attempt?.photos?.some((value) => value.id === id)) {
                  setDiscardedPhotos((old) => [...new Set([...old, id])]);
                } else {
                  void api(`/assets/${id}`, { method: 'DELETE' }).catch(() =>
                    setError(
                      '照片已从本轮移除，但上传文件暂未删除，可稍后在账号中清理。',
                    ),
                  );
                }
              }}
              onError={setError}
              onUploading={setUploading}
            />
            <div className="input-divider">
              <span>也可以用文字开始</span>
            </div>
            <label className="text-label" htmlFor="product-description">
              或者，描述一下你的产品
            </label>
            <Input.TextArea
              id="product-description"
              placeholder="例如：像一台苹果手机，背面有两个镜头；也可以补充品牌或你注意到的特点…"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              autoSize={{ minRows: 3 }}
              maxLength={10000}
            />
            {!!rejectedCandidates.length && (
              <Alert
                type="info"
                title="已排除这些候选，请补充文字或照片，再继续判断。"
              />
            )}
            <div className="identify-footer">
              <small>
                <ShieldCheck size={14} aria-hidden="true" />
                照片保持私有，仅用于识别；将发送至模型服务处理。
              </small>
              <Button
                type="primary"
                aria-label={attempt ? '综合照片继续判断' : '开始识别'}
                loading={busy}
                disabled={
                  busy ||
                  uploading ||
                  (!photos.length && !input.trim()) ||
                  (!!attempt &&
                    ['queued', 'processing'].includes(attempt.status)) ||
                  (attempt?.roundNo ?? 0) >= (attempt?.maxRounds ?? 4) ||
                  (attempt?.status === 'waiting_for_user' &&
                    !input.trim() &&
                    !discardedPhotos.length &&
                    !photos.some(
                      (photo) =>
                        !attempt.photos?.some((old) => old.id === photo.id),
                    ))
                }
                onClick={() => void identify()}
              >
                {attempt ? '综合照片继续判断' : '开始识别'}
                <ChevronRight size={16} />
              </Button>
            </div>
            {attempt && (
              <div>
                <RecognitionResult
                  attempt={attempt}
                  busy={busy || uploading}
                  onConfirm={(candidate) => void confirm(candidate)}
                  onRejectAll={() => {
                    const rejected = attempt.candidates.flatMap((candidate) => {
                      const id =
                        candidate.candidateId ?? candidate.productModelId;
                      return id ? [id] : [];
                    });
                    setRejectedCandidates(rejected);
                    rememberRejections(attempt.id, rejected);
                    document.getElementById('product-description')?.focus();
                  }}
                  onRestart={restartIdentification}
                  onAddPhotos={() =>
                    document
                      .querySelector<HTMLInputElement>(
                        '#recognition-photos input[type=file]',
                      )
                      ?.click()
                  }
                />
                {['waiting_for_user', 'failed'].includes(attempt.status) && (
                  <>
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
                <strong>先拍外观，再一起缩小范围</strong>
                <p>
                  先拍完整正面或背面。系统会比较相近产品，再告诉你下一张拍哪里更有用；没有铭牌也能继续。
                </p>
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
        <div key={product.id} className="product-workbench">
          <div className="product-tools" aria-label="设备资料与工具">
            <ManualPanel product={product} />
            <MemoryPanel instanceId={product.id} />
            <GuidancePanel product={product} onBusyChange={setGuidanceBusy} />
          </div>
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
    </main>
  );
}
