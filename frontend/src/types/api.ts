/** 公共响应类型；仅保留 API 对当前用户公开的字段。 */
export interface User {
  id: string;
  email: string;
  displayName?: string | null;
}
export interface Asset {
  id: string;
  mimeType: string;
  sizeBytes: number;
  status: string;
  kind: string;
  readUrl?: string | null;
}
export interface Candidate {
  productModelId: string;
  brandName: string;
  modelCode: string;
  confidence: number;
  evidence: string;
}
export interface Identification {
  id: string;
  status: string;
  candidates: Candidate[];
  clarification: string | null;
  jobId: string;
}
export interface ProductInstance {
  id: string;
  productModelId: string;
  nickname: string | null;
  modelCode: string;
  brandName: string;
  identityStatus: string;
  verificationStatus: string;
  knowledgeJobId: string | null;
  knowledgeStatus: string;
}
export interface Job {
  id: string;
  status: string;
  progress: { stage?: string; percent?: number };
  lastError: string | null;
}
export interface Citation {
  knowledgeChunkId: string;
  documentVersionId: string;
  title: string;
  url: string;
  pageStart: number;
  pageEnd: number;
  rank: number;
  verificationStatus: string;
  verifiedAt?: string | null;
}
export interface WebSource {
  id: string;
  rank: number;
  title: string;
  url: string;
  sourceKind: 'official' | 'third_party' | 'unverified_official';
  fetchedAt: string;
  pageStart: number | null;
  pageEnd: number | null;
}
export interface WebLookup {
  status: string;
  reason: string | null;
  searchedAt: string;
  queryKey: string;
  cached: boolean;
  conflict?: { title: string; url: string };
}
export interface Message {
  id: string;
  role: string;
  content: string | null;
  answerBasis?:
    | 'verified'
    | 'web'
    | 'general'
    | 'clarification'
    | 'refused'
    | 'insufficient'
    | null;
  citations: Citation[];
  webLookup?: WebLookup | null;
  webSources?: WebSource[];
  assets?: Asset[];
  runId?: string | null;
  feedback?: {
    feedbackType: 'helpful' | 'incorrect';
    note: string | null;
  } | null;
}
export interface Conversation {
  id: string;
  title: string | null;
  userProductInstanceId: string;
  status: string;
  createdAt: string;
}
export interface MessagePage {
  items: Message[];
  nextCursor: string | null;
}
export interface ManualSection {
  id: string;
  parentId: string | null;
  sectionKey: string;
  title: string;
  sortOrder: number;
  body: string | null;
  generationStatus: string;
  citations: Citation[];
  reused?: boolean;
  personalNotes?: { text: string }[];
}
export interface Manual {
  id: string;
  status: string;
  version: number;
  jobId: string | null;
  sections: ManualSection[];
  visibility?: 'public' | 'private';
  inputsStale?: boolean;
  contextStale?: boolean;
  personalContext?: { memoryType: Memory['memoryType']; content: string }[];
  coverage?: { totalChunks: number; citedChunks: number };
  latestBuild?: {
    id: string;
    version: number;
    status: string;
    jobId: string | null;
    inputsStale?: boolean;
  } | null;
}
export interface Memory {
  id: string;
  memoryType: 'preference' | 'product_fact' | 'resume_summary';
  scope: 'global' | 'instance';
  userProductInstanceId: string | null;
  content: string;
  consentStatus: 'pending' | 'confirmed' | 'revoked';
  confirmedAt: string | null;
  expiresAt: string | null;
}
export interface Guidance {
  id: string;
  topic: string;
  status: 'active' | 'paused' | 'completed' | 'failed' | 'blocked_unsafe';
  riskLevel: 'low' | 'medium' | 'high';
  version: number;
  currentStepNo: number | null;
  notice: string | null;
  currentStep: {
    id: string;
    stepNo: number;
    instruction: string;
    successCriteria: string;
    riskLevel: 'low' | 'medium';
    preconditions: string[];
    safetyNotes: string[];
    failureOfStepId: string | null;
    status: string;
    citations: Citation[];
  } | null;
}
export interface Run {
  id: string;
  status: string;
  message?: Message | null;
  lastError?: string | null;
}
