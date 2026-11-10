<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import MarkdownIt from "markdown-it";
import dayjs from "dayjs";
import {
  createChatSession,
  deleteChatSession,
  getChatPricing,
  getChatUsage,
  listChatMessages,
  listChatSessions
} from "@/api/chat";
import { streamChatMessage, type StreamHandle } from "@/composables/useChatStream";
import { useUserStoreHook } from "@/store/modules/user";
import type { ChatPricing, ChatSessionInfo, ChatUsageInfo, ToolProgress } from "@/types/chat";

defineOptions({ name: "Chat" });

const userStore = useUserStoreHook();

/**
 * 智能问答页（原生 Vue UI，替代旧 iframe 方案）。
 * 左：会话列表；右：消息流（Markdown + 工具进度）+ 输入区。
 * SSE 经 rag-api 流式代理到 Hermes，浏览器不直接接触 Hermes。
 */

// ── Markdown（关闭原始 HTML 防注入；链接新窗口打开）─────────────

const route = useRoute();
const router = useRouter();

// ── Markdown（关闭原始 HTML 防注入；链接新窗口打开）─────────────
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener");
  return defaultLinkOpen(tokens, idx, options, env, self);
};
const renderMd = (text: string) => md.render(text || "");

// ── 状态 ─────────────────────────────────────────────────
interface UIMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  toolEvents?: ToolProgress[];
  failed?: boolean;
  /** Hermes 上游 usage（仅 assistant 消息有） */
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  created_at?: string;
}

const QUICK_PROMPTS = [
  "某企业最近一次巡查发现了哪些隐患？",
  "帮我查一下有限空间企业有多少家",
  "总结一下本月巡查整改情况"
];

const sessions = ref<ChatSessionInfo[]>([]);
const currentId = ref<string | null>(null);
const messages = ref<UIMessage[]>([]);
const input = ref("");
const streaming = ref(false);
const loading = ref(false);
const pageHeight = ref<number | null>(null);
const pricing = ref<ChatPricing | null>(null);
const usage = ref<ChatUsageInfo | null>(null);
// 正在生成中的回答 token 估算值（usage 帧只在流末尾返回，生成期间用文本长度粗估）
const liveTokens = ref(0);

let streamHandle: StreamHandle | null = null;
let resizeObserver: ResizeObserver | null = null;
const pageRef = ref<HTMLElement | null>(null);
const messageScroll = ref<any>(null);

const formatSessionTime = (iso: string) => dayjs(iso).format("YYYY-MM-DD");

// ── token / 费用统计（当前会话累计）─────────────────────────
/** 单段文本的 token 估算：中文近似 1 字 1 token，其余按 4 字符 1 token */
const estimateDeltaTokens = (text: string) => {
  const cjk = (text.match(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/g) ?? [])
    .length;
  return cjk + Math.ceil((text.length - cjk) / 4);
};
const formatTokens = (n: number) => Math.round(n).toLocaleString("en-US");

/** 已落库消息的 token 累计（usage 帧落库后由后端回读） */
const sessionUsage = computed(() => {
  let prompt = 0;
  let completion = 0;
  for (const m of messages.value) {
    if (m.role !== "assistant") continue;
    prompt += m.prompt_tokens ?? 0;
    completion += m.completion_tokens ?? 0;
  }
  return { prompt, completion };
});

/** 含"生成中"估算值的会话总用量 */
const usageTotal = computed(() => {
  const { prompt, completion } = sessionUsage.value;
  return {
    prompt,
    completion: completion + liveTokens.value,
    total: prompt + completion + liveTokens.value
  };
});

/** 会话累计费用（人民币，按后端配置的单价换算） */
const usageCost = computed(() => {
  if (!pricing.value || !usageTotal.value.total) return "";
  const { prompt, completion } = usageTotal.value;
  const cny =
    (prompt / 1e6) * pricing.value.input +
    (completion / 1e6) * pricing.value.output;
  return `¥${cny.toFixed(4)}`;
});

// ── 布局高度实测（平台内容区是 el-scrollbar + footer 结构）──────
const updateHeight = () => {
  const el = pageRef.value;
  if (!el) return;
  const wrap = el.closest(".el-scrollbar__wrap") as HTMLElement | null;
  if (!wrap) {
    pageHeight.value = null;
    return;
  }
  const view = wrap.querySelector(".el-scrollbar__view") as HTMLElement | null;
  const footer = view?.querySelector("footer") as HTMLElement | null;
  const cs = getComputedStyle(el);
  const margin =
    parseFloat(cs.marginTop) + parseFloat(cs.marginBottom);
  pageHeight.value = Math.max(
    Math.floor(wrap.clientHeight - (footer?.offsetHeight ?? 0) - margin),
    360
  );
};

// ── 会话加载 / 切换 ───────────────────────────────────────
const loadSessions = async () => {
  try {
    sessions.value = await listChatSessions();
  } catch {
    /* http 拦截器已有全局提示 */
  }
};

const openSession = async (id: string) => {
  if (streaming.value) {
    ElMessage.warning("请先等待或停止当前回答");
    return;
  }
  currentId.value = id;
  loading.value = true;
  try {
    const list = await listChatMessages(id);
    messages.value = list.map(m => ({ ...m }));
    await scrollToBottom();
  } catch {
    // 会话已被删除或接口失败：回到空态，避免在失效 id 上继续发消息
    currentId.value = null;
    messages.value = [];
    if (route.params.sessionId) router.replace("/chat");
  } finally {
    loading.value = false;
  }
};

const selectSession = (id: string) => {
  if (id === currentId.value) return;
  router.replace(id ? `/chat/${id}` : "/chat");
};

const newSession = async () => {
  if (streaming.value) return;
  try {
    const s = await createChatSession();
    sessions.value.unshift(s);
    router.replace(`/chat/${s.id}`);
    messages.value = [];
    currentId.value = s.id;
  } catch {
    /* 全局拦截器提示 */
  }
};

const removeSession = async (id: string) => {
  try {
    await ElMessageBox.confirm("删除后该会话的全部消息不可恢复，确定删除？", "删除会话", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消"
    });
  } catch {
    return;
  }
  await deleteChatSession(id);
  sessions.value = sessions.value.filter(s => s.id !== id);
  if (currentId.value === id) {
    const next = sessions.value[0] ?? null;
    currentId.value = null;
    messages.value = [];
    if (next) router.replace(`/chat/${next.id}`);
    else router.replace("/chat");
  }
  ElMessage.success("已删除");
};

// 路由参数 → 会话（keepAlive 下只挂载一次，靠 watch 切换）
watch(
  () => route.params.sessionId,
  id => {
    const sid = typeof id === "string" ? id : "";
    if (sid) {
      if (sid !== currentId.value) openSession(sid);
    } else {
      currentId.value = null;
      messages.value = [];
    }
  },
  { immediate: false }
);

// ── 发送 / 停止 ──────────────────────────────────────────
const upsertToolEvent = (target: UIMessage, p: ToolProgress) => {
  const list = (target.toolEvents ??= []);
  const existing = list.find(e => e.toolCallId === p.toolCallId);
  if (existing) Object.assign(existing, p);
  else list.push(p);
};

const send = async (preset?: string) => {
  const content = (preset ?? input.value).trim();
  if (!content || streaming.value) return;

  // 配额检查：前端拦截 + 后端 send_message 也会再查一次
  if (usage.value && !usage.value.allowed) {
    ElMessage.warning(usage.value.reason || "对话功能暂不可用");
    return;
  }

  // 首次发送自动建会话。
  // 注意：此处不能立即 router.replace 到 /chat/:id —— 布局用 :key="fullPath"
  // 渲染页面，fullPath 一变整个组件会被销毁重建，进行中的 SSE 会被
  // onBeforeUnmount 中止、本地气泡也会丢失。URL 在流式结束后再更新（见 onDone）。
  if (!currentId.value) {
    try {
      const s = await createChatSession();
      sessions.value.unshift(s);
      currentId.value = s.id;
    } catch {
      return;
    }
  }
  const sessionId = currentId.value as string;

  messages.value.push({ role: "user", content });
  messages.value.push({ role: "assistant", content: "" });
  // 必须取 reactive 数组返回的代理引用：若持有 push 前的原始对象，
  // 通过原始引用改内容不经过 Proxy trap，增量文本/工具行不会触发渲染。
  const assistantMsg = messages.value[messages.value.length - 1];
  input.value = "";
  streaming.value = true;
  liveTokens.value = 0;
  await scrollToBottom();

  streamHandle = streamChatMessage(sessionId, content, {
    onDelta: delta => {
      assistantMsg.content += delta;
      // usage 帧只在流末尾返回，生成期间用文本长度粗估当前回答的 token 数
      liveTokens.value += estimateDeltaTokens(delta);
      void autoScroll();
    },
    onTool: p => {
      upsertToolEvent(assistantMsg, p);
      void scrollToBottom();
    },
    onUsage: u => {
      assistantMsg.prompt_tokens = u.prompt_tokens;
      assistantMsg.completion_tokens = u.completion_tokens;
      assistantMsg.total_tokens = u.total_tokens;
      liveTokens.value = 0;
    },
    onDone: () => {
      streaming.value = false;
      streamHandle = null;
      liveTokens.value = 0;
      // 标题/updated_at 在首条消息后变化，刷新侧边栏
      void loadSessions();
      // 流式结束后再同步 URL：fullPath 变化会让组件以新 key 重建并从后端
      // 拉回已落库的消息。延迟 500ms：assistant 在服务端响应结束后的 finally
      // 中落库，立刻 GET 历史可能竞态读不到最后一条。
      if (route.params.sessionId !== sessionId) {
        setTimeout(() => router.replace(`/chat/${sessionId}`), 500);
      }
    },
    onError: message => {
      streaming.value = false;
      streamHandle = null;
      liveTokens.value = 0;
      assistantMsg.failed = true;
      ElMessage.error(message);
      void loadSessions();
      if (route.params.sessionId !== sessionId) {
        setTimeout(() => router.replace(`/chat/${sessionId}`), 500);
      }
    }
  });
};

const stop = () => {
  streamHandle?.abort();
  streamHandle = null;
  streaming.value = false;
  liveTokens.value = 0;
  // 空回答（连接即失败被 abort）移除占位气泡
  const last = messages.value[messages.value.length - 1];
  if (last && last.role === "assistant" && !last.content && !last.toolEvents?.length) {
    messages.value.pop();
  }
};

const onKeydown = (e: KeyboardEvent) => {
  // Enter 发送，Shift+Enter 换行（IME 组词期间不触发）
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    void send();
  }
};

// ── 滚动 ─────────────────────────────────────────────────
const scrollToBottom = async () => {
  await nextTick();
  const wrap = messageScroll.value?.wrapRef as HTMLElement | undefined;
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
};
let autoTimer: ReturnType<typeof setTimeout> | null = null;
const autoScroll = async () => {
  if (autoTimer) return; // 同一 tick 内合并
  autoTimer = setTimeout(() => {
    autoTimer = null;
    void scrollToBottom();
  }, 0);
};

// keepAlive 下登出换账号后组件从缓存恢复，onActivated 时重新拉后端数据
// （后端天然按 user_id 隔离，只会返回当前登录用户的会话）
const mountedUsername = ref<string>("");

onMounted(async () => {
  mountedUsername.value = userStore.username;
  updateHeight();
  const wrap = pageRef.value?.closest(".el-scrollbar__wrap") as HTMLElement | null;
  if (wrap && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(updateHeight);
    resizeObserver.observe(wrap);
  }
  window.addEventListener("resize", updateHeight);
  requestAnimationFrame(updateHeight);

  // 计费单价（失败则仅展示 token 数，不展示费用）
  getChatPricing()
    .then(p => {
      pricing.value = p;
    })
    .catch(() => {
      /* 静默：无单价只显示 token */
    });

  // 对话配额 / 用量（超限/锁定时禁用输入 + 底部栏显示今日剩余）
  getChatUsage()
    .then(u => {
      usage.value = u;
    })
    .catch(() => {
      /* 静默：拉不到就不显示配额 */
    });

  await loadSessions();
  const sid = route.params.sessionId;
  if (typeof sid === "string" && sid) await openSession(sid);
});

onActivated(async () => {
  // keepAlive 缓存恢复时，如果登录用户变了，重新拉后端数据（后端按 user_id 隔离）
  if (mountedUsername.value && mountedUsername.value !== userStore.username) {
    mountedUsername.value = userStore.username;
    messages.value = [];
    currentId.value = null;
    router.replace("/chat");
    try {
      await loadSessions();
      await getChatUsage().then(u => (usage.value = u)).catch(() => {});
    } catch {
      /* 静默 */
    }
  }
});

onBeforeUnmount(() => {
  streamHandle?.abort();
  resizeObserver?.disconnect();
  window.removeEventListener("resize", updateHeight);
});
</script>

<template>
  <div
    ref="pageRef"
    class="chat-page"
    :style="pageHeight ? { height: `${pageHeight}px` } : {}"
  >
    <!-- ── 左侧会话栏 ── -->
    <aside class="chat-sidebar">
      <el-button
        type="primary"
        class="chat-sidebar__new"
        @click="newSession"
      >
        ＋ 新建会话
      </el-button>
      <el-scrollbar class="chat-sidebar__list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-item"
          :class="{ 'is-active': s.id === currentId }"
          @click="selectSession(s.id)"
        >
          <div class="session-item__title">{{ s.title }}</div>
          <div class="session-item__time">
            {{ formatSessionTime(s.updated_at) }}
          </div>
          <el-icon
            class="session-item__del"
            title="删除会话"
            @click.stop="removeSession(s.id)"
          >
            <svg viewBox="0 0 1024 1024" width="14" height="14">
              <path
                fill="currentColor"
                d="M160 256H96a32 32 0 0 1 0-64h256V95.936a32 32 0 0 1 32-32h256a32 32 0 0 1 32 32V192h256a32 32 0 1 1 0 64h-64v672a32 32 0 0 1-32 32H192a32 32 0 0 1-32-32V256zm448-64v-64H416v64h192zM224 896h576V256H224v640zm192-128a32 32 0 0 1-32-32V416a32 32 0 0 1 64 0v320a32 32 0 0 1-32 32zm192 0a32 32 0 0 1-32-32V416a32 32 0 0 1 64 0v320a32 32 0 0 1-32 32z"
              />
            </svg>
          </el-icon>
        </div>
        <el-empty
          v-if="!sessions.length"
          description="还没有会话"
          :image-size="80"
        />
      </el-scrollbar>
    </aside>

    <!-- ── 右侧主区 ── -->
    <section class="chat-main">
      <el-scrollbar ref="messageScroll" class="chat-messages">
        <!-- 空态欢迎 -->
        <div v-if="!messages.length && !loading" class="chat-welcome">
          <div class="chat-welcome__title">智能问答助手</div>
          <div class="chat-welcome__sub">
            已接入企业台账、巡查记录与知识库文档，可直接提问，例如：
          </div>
          <div class="chat-welcome__prompts">
            <div
              v-for="p in QUICK_PROMPTS"
              :key="p"
              class="prompt-chip"
              @click="send(p)"
            >
              {{ p }}
            </div>
          </div>
        </div>

        <div v-else class="msg-list">
          <template v-for="(m, idx) in messages" :key="m.id ?? `local-${idx}`">
            <!-- 工具进度（assistant 气泡上方） -->
            <div
              v-if="m.toolEvents?.length"
              class="tool-events"
              :class="{ 'is-user': m.role === 'user' }"
            >
              <div v-for="t in m.toolEvents" :key="t.toolCallId" class="tool-event">
                <span v-if="t.status === 'running'" class="tool-event__spin">◌</span>
                <span v-else class="tool-event__done">✓</span>
                <span class="tool-event__emoji">{{ t.emoji || "🔧" }}</span>
                <span class="tool-event__label">{{ t.label || t.tool }}</span>
              </div>
            </div>

            <div class="msg-row" :class="`is-${m.role}`">
              <div v-if="m.role === 'assistant'" class="msg-avatar">
                <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm0 10c4.42 0 8 2.02 8 4.5V20H4v-1.5C4 16.02 7.58 14 12 14Z"
                  />
                </svg>
              </div>
              <div class="msg-bubble" :class="{ 'is-failed': m.failed }">
                <div
                  v-if="m.role === 'assistant'"
                  class="md-body"
                  v-html="renderMd(m.content || (streaming && idx === messages.length - 1 ? ' ' : ''))"
                />
                <div v-else class="msg-text">{{ m.content }}</div>
                <div v-if="m.failed" class="msg-failed-tip">
                  回答生成失败，请修改问题后重新发送
                </div>
              </div>
              <div v-if="m.role === 'user'" class="msg-avatar is-user">
                <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm0 10c4.42 0 8 2.02 8 4.5V20H4v-1.5C4 16.02 7.58 14 12 14Z"
                  />
                </svg>
              </div>
            </div>
          </template>
        </div>
      </el-scrollbar>

      <!-- 输入区 -->
      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          resize="none"
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          :disabled="!!usage && !usage.allowed"
          @keydown="onKeydown"
        />
        <div class="chat-input__bar">
          <div class="chat-input__meta">
            <span class="chat-input__hint">回答由 Hermes Agent 生成，请注意甄别</span>
            <span v-if="usageTotal.total > 0" class="chat-input__usage">
              <span class="usage-chip">
                本会话 {{ formatTokens(usageTotal.total) }} tokens
                <span class="usage-chip__split">
                  （输入 {{ formatTokens(usageTotal.prompt) }} / 输出
                  {{ formatTokens(usageTotal.completion) }}）
                </span>
              </span>
              <span v-if="usageCost" class="usage-chip is-cost">
                累计 {{ usageCost }}{{ streaming ? " · 生成中" : "" }}
              </span>
            </span>
            <span v-if="usage" class="chat-input__usage">
              <!-- 管理员：显示"管理员不受限" -->
              <span v-if="usage.limit_yuan === null && !usage.locked" class="usage-chip is-admin">
                管理员不受限
              </span>
              <!-- 普通用户：显示今日剩余 -->
              <span v-else-if="usage.limit_yuan !== null" class="usage-chip">
                今日剩余 ¥{{ usage.remaining_yuan?.toFixed(2) }}
                <span class="usage-chip__split">
                  / 限额 ¥{{ usage.limit_yuan.toFixed(2) }}
                </span>
              </span>
              <!-- 超限提示 -->
              <span v-if="!usage.allowed && !usage.locked" class="usage-chip is-over">
                今日已达上限
              </span>
              <!-- 锁定提示 -->
              <span v-if="usage.locked" class="usage-chip is-locked">
                对话已被锁定
              </span>
            </span>
          </div>
          <el-button
            v-if="streaming"
            type="danger"
            plain
            @click="stop"
          >
            停止
          </el-button>
          <el-button
            v-else
            type="primary"
            :disabled="!input.trim() || (!!usage && !usage.allowed)"
            @click="send()"
          >
            发送
          </el-button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  /* 不写 width:100%：根元素带平台 .main-content 的 24px 左右外边距，
     100% 会额外多出 48px 造成横向溢出、右侧被 overflow-x:hidden 裁掉 */
  min-height: 360px;
  gap: 12px;
  /* 兜底，挂载后由 JS 实测覆盖 */
  height: calc(100vh - 158px);
}

/* ── 会话侧栏 ── */
.chat-sidebar {
  display: flex;
  flex-direction: column;
  width: 240px;
  flex-shrink: 0;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 8px;
  padding: 12px;
}
.chat-sidebar__new {
  width: 100%;
  margin-bottom: 10px;
}
.chat-sidebar__list {
  flex: 1;
  min-height: 0;
}
.session-item {
  position: relative;
  padding: 8px 28px 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.session-item:hover {
  background: var(--el-fill-color-light, #f5f7fa);
}
.session-item.is-active {
  background: var(--el-color-primary-light-9, #ecf5ff);
}
.session-item__title {
  font-size: 13px;
  color: var(--el-text-color-primary, #303133);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-item__time {
  font-size: 11px;
  color: var(--el-text-color-secondary, #909399);
  margin-top: 2px;
}
.session-item__del {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--el-text-color-placeholder, #a8abb2);
  opacity: 0;
  transition: opacity 0.15s, color 0.15s;
}
.session-item:hover .session-item__del {
  opacity: 1;
}
.session-item__del:hover {
  color: var(--el-color-danger, #f56c6c);
}

/* ── 主区 ── */
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 8px;
  overflow: hidden;
}
.chat-messages {
  flex: 1;
  min-height: 0;
  padding: 16px 20px;
}

/* 空态 */
.chat-welcome {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.chat-welcome__title {
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
}
.chat-welcome__sub {
  font-size: 13px;
}
.chat-welcome__prompts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  width: 480px;
  max-width: 90%;
}
.prompt-chip {
  padding: 10px 14px;
  border: 1px solid var(--el-border-color, #dcdfe6);
  border-radius: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
  cursor: pointer;
  transition: all 0.15s;
}
.prompt-chip:hover {
  border-color: var(--el-color-primary, #409eff);
  color: var(--el-color-primary, #409eff);
  background: var(--el-color-primary-light-9, #ecf5ff);
}

/* ── 消息气泡 ── */
.msg-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.msg-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.msg-row.is-user {
  justify-content: flex-end;
}
/* 两侧小人头像 */
.msg-avatar {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 助手：中性灰底 + 白色人像，保证清晰可见；.is-user 覆盖为用户主题蓝 */
  background: var(--el-text-color-secondary, #909399);
  color: #fff;
}
.msg-avatar.is-user {
  background: var(--el-color-primary, #409eff);
  color: #fff;
}
.msg-bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
}
.msg-row.is-assistant .msg-bubble {
  background: var(--el-fill-color-light, #f5f7fa);
  color: var(--el-text-color-primary, #303133);
  border-top-left-radius: 2px;
}
.msg-row.is-user .msg-bubble {
  background: var(--el-color-primary, #409eff);
  color: #fff;
  border-top-right-radius: 2px;
}
.msg-text {
  white-space: pre-wrap;
}
.msg-bubble.is-failed {
  border: 1px solid var(--el-color-danger-light-5, #fab6b6);
}
.msg-failed-tip {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-danger, #f56c6c);
}

/* ── 工具进度 ── */
.tool-events {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 2px 0 -6px;
}
.tool-event {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.tool-event__spin {
  display: inline-block;
  animation: chat-spin 1.2s linear infinite;
  color: var(--el-color-primary, #409eff);
}
.tool-event__done {
  color: var(--el-color-success, #67c23a);
}
@keyframes chat-spin {
  to {
    transform: rotate(360deg);
  }
}

/* ── Markdown 排版 ── */
.md-body :deep(p) {
  margin: 0 0 8px;
}
.md-body :deep(p:last-child) {
  margin-bottom: 0;
}
.md-body :deep(pre) {
  background: rgba(0, 0, 0, 0.06);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  margin: 8px 0;
}
.md-body :deep(code) {
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 12.5px;
}
.md-body :deep(:not(pre) > code) {
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 5px;
  border-radius: 4px;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 22px;
  margin: 6px 0;
}
.md-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--el-border-color, #dcdfe6);
  padding: 4px 10px;
  font-size: 13px;
}

/* ── 输入区 ── */
.chat-input {
  flex-shrink: 0;
  border-top: 1px solid var(--el-border-color-lighter, #e4e7ed);
  padding: 12px 16px 10px;
}
.chat-input__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.chat-input__hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder, #a8abb2);
}
.chat-input__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.chat-input__usage {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.usage-chip {
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}
.usage-chip.is-cost {
  color: var(--el-color-primary, #409eff);
}
.usage-chip.is-admin {
  color: var(--el-color-success, #67c23a);
}
.usage-chip.is-over {
  color: var(--el-color-warning, #e6a23c);
}
.usage-chip.is-locked {
  color: var(--el-color-danger, #f56c6c);
}
.usage-chip__split {
  color: var(--el-text-color-placeholder, #a8abb2);
}
</style>
