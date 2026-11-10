/**
 * 智能问答 SSE 流式 composable。
 *
 * 不能用 EventSource：OpenAI 兼容协议是 POST 且要带平台 JWT，
 * 因此用 fetch + ReadableStream + TextDecoder 手动解析 SSE。
 *
 * 后端 rag-api 原样透传 Hermes 的帧，帧形态（已实测）：
 *   data: {OpenAI chat.completion.chunk}      无 event 行 → 文本 delta
 *   event: hermes.tool.progress
 *   data: {tool,emoji,label,toolCallId,status}            → 工具进度
 *   data: {...,choices:[{delta:{},finish_reason:"stop"}],usage:{...}} → token 用量
 *   data: [DONE]                                           → 结束
 *
 * SSE chunk 可能在任意位置截断，必须 buffer 后按空行分帧。
 */
import { formatToken, getToken } from "@/utils/auth";
import type { TokenUsage, ToolProgress } from "@/types/chat";

export interface StreamHandlers {
  /** 增量文本（逐 token） */
  onDelta: (text: string) => void;
  /** 工具调用进度（running / completed） */
  onTool: (progress: ToolProgress) => void;
  /** 上游 usage 帧的 token 用量（流末尾，每次回答至多一帧） */
  onUsage?: (usage: TokenUsage) => void;
  /** 流正常结束（[DONE] 或连接关闭） */
  onDone: () => void;
  /** 出错（网络/HTTP 错误码/上游 error 帧）；主动 abort 不触发 */
  onError: (message: string) => void;
}

export interface StreamHandle {
  /** 中断接收（用户点"停止"）；后端会把已生成部分落库 */
  abort: () => void;
  /** 流结束/失败的 Promise，供调用方 await 收尾 */
  done: Promise<void>;
}

const TOOL_PROGRESS_EVENT = "hermes.tool.progress";

export function streamChatMessage(
  sessionId: string,
  content: string,
  handlers: StreamHandlers
): StreamHandle {
  const controller = new AbortController();
  let finished = false;
  let aborted = false;

  const finish = (errorMessage?: string) => {
    if (finished) return;
    finished = true;
    if (errorMessage) handlers.onError(errorMessage);
    else handlers.onDone();
  };

  const run = async () => {
    let resp: Response;
    try {
      resp = await fetch(`/chat/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: formatToken(getToken().accessToken)
        },
        body: JSON.stringify({ content }),
        signal: controller.signal
      });
    } catch (e) {
      if (!aborted) finish("网络连接失败，请稍后重试");
      return;
    }

    // 建连失败：后端以 JSON 返回 {detail}
    if (!resp.ok || !resp.body) {
      let message = `服务异常（${resp.status}）`;
      try {
        const j = await resp.json();
        if (j?.detail) message = j.detail;
      } catch {
        /* 非 JSON 错误体，沿用状态码提示 */
      }
      finish(message);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    /** 处理一个完整 SSE 帧（可能含 event:/data:/其它元数据行） */
    const handleFrame = (frame: string) => {
      let eventName = "";
      const dataLines: string[] = [];
      for (const rawLine of frame.split(/\r?\n/)) {
        if (rawLine.startsWith("event:")) {
          eventName = rawLine.slice(6).trim();
        } else if (rawLine.startsWith("data:")) {
          // SSE 规范：单个帧可有多行 data:，以 \n 拼接
          dataLines.push(rawLine.slice(5).trimStart());
        }
      }
      const data = dataLines.join("\n");
      if (!data) return;

      if (data === "[DONE]") {
        finish();
        return;
      }

      let obj: any;
      try {
        obj = JSON.parse(data);
      } catch {
        return;
      }

      if (eventName === TOOL_PROGRESS_EVENT) {
        handlers.onTool(obj as ToolProgress);
        return;
      }

      if (obj?.error) {
        finish(typeof obj.error === "string" ? obj.error : "问答服务返回错误");
        return;
      }

      // 末尾 usage 帧（choices[0].delta 为空 + finish_reason=stop + usage）
      if (obj?.usage && typeof obj.usage === "object") {
        handlers.onUsage?.(obj.usage as TokenUsage);
        return;
      }

      const delta: unknown = obj?.choices?.[0]?.delta?.content;
      if (typeof delta === "string" && delta) {
        handlers.onDelta(delta);
      }
    };

    try {
      while (true) {
        const { value, done: readerDone } = await reader.read();
        if (readerDone) break;
        buffer += decoder.decode(value, { stream: true });
        // 统一 CRLF 后按空行分帧；最后不完整的片段留在 buffer
        buffer = buffer.replace(/\r\n/g, "\n");
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          if (frame.trim()) handleFrame(frame);
        }
      }
      if (buffer.trim()) handleFrame(buffer);
      // 没收到显式 [DONE] 但连接关闭：按正常结束收尾
      finish();
    } catch (e) {
      if (!aborted) finish("流式接收中断，请重试");
    }
  };

  return {
    abort: () => {
      aborted = true;
      controller.abort();
    },
    done: run()
  };
}
