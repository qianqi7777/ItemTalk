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
export interface Message {
  id: string;
  role: string;
  content: string | null;
  citations: Citation[];
  assets?: Asset[];
  runId?: string | null;
}
export interface Run {
  id: string;
  status: string;
  message?: Message | null;
  lastError?: string | null;
}
