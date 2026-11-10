import { http } from "@/utils/http";
import type { ChatMessageInfo, ChatPricing, ChatSessionInfo, ChatUsageInfo } from "@/types/chat";

/** 后端统一响应包装 */
interface ApiResult<T> {
  success: boolean;
  data: T;
}

/** 列出当前用户的问答会话（updated_at 倒序） */
export const listChatSessions = () =>
  http
    .request<ApiResult<ChatSessionInfo[]>>("get", "/chat/sessions")
    .then(res => res.data);

/** 新建问答会话（默认标题"新对话"，首条消息后后端自动改名） */
export const createChatSession = (title?: string) =>
  http
    .request<ApiResult<ChatSessionInfo>>("post", "/chat/sessions", {
      data: { title: title ?? null }
    })
    .then(res => res.data);

/** 删除会话（后端级联删除消息） */
export const deleteChatSession = (id: string) =>
  http.request<ApiResult<null>>("delete", `/chat/sessions/${id}`);

/** 拉取会话历史消息（时间正序，最新 100 条） */
export const listChatMessages = (id: string) =>
  http
    .request<ApiResult<ChatMessageInfo[]>>(
      "get",
      `/chat/sessions/${id}/messages`
    )
    .then(res => res.data);

/** 问答计费单价（人民币 / 百万 tokens），前端据此换算费用 */
export const getChatPricing = () =>
  http
    .request<ApiResult<ChatPricing>>("get", "/chat/pricing")
    .then(res => res.data);

/** 当前用户的对话配额 / 用量（今日剩余、限额、锁定状态） */
export const getChatUsage = () =>
  http
    .request<ApiResult<ChatUsageInfo>>("get", "/chat/usage")
    .then(res => res.data);
