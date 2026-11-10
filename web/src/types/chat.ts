/** 智能问答模块类型定义 */

/** 问答会话（后端 chat_session 表） */
export interface ChatSessionInfo {
  /** UUID */
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** 问答消息（后端 chat_message 表，role 仅 user/assistant） */
export interface ChatMessageInfo {
  /** UUID；乐观插入的本地消息可无 id */
  id?: string;
  role: "user" | "assistant";
  content: string;
  /** Hermes 上游 usage（仅 assistant 消息有；user / 中断无 usage 时为 null） */
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  created_at?: string;
}

/** Hermes SSE 末尾 usage 帧的 token 用量 */
export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/** 问答计费单价（人民币 / 百万 tokens） */
export interface ChatPricing {
  currency: string;
  unit: string;
  input: number;
  output: number;
}

/** 对话配额 / 用量（GET /chat/usage 响应） */
export interface ChatUsageInfo {
  /** 是否允许发送（管理员始终 true；超限/锁定则 false） */
  allowed: boolean;
  /** 拒绝原因（仅 allowed=false 时有值） */
  reason: string | null;
  /** 今日已用费用（元） */
  usage_yuan: number;
  /** 每日限额（元），管理员为 null */
  limit_yuan: number | null;
  /** 今日剩余（元），管理员为 null */
  remaining_yuan: number | null;
  /** 是否被管理员手动锁定 */
  locked: boolean;
}

/** Hermes SSE 工具进度帧（event: hermes.tool.progress 的 data） */
export interface ToolProgress {
  /** 工具名，如 terminal / rag_docs_search */
  tool: string;
  /** 工具 emoji */
  emoji?: string;
  /** 动作摘要，如正在执行的命令 */
  label?: string;
  /** Hermes 侧调用 id，running/completed 两帧共用 */
  toolCallId: string;
  /** running / completed */
  status: "running" | "completed" | string;
}
