const Layout = () => import("@/layout/index.vue");

export default {
  path: "/chat-page",
  name: "Chat",
  component: Layout,
  redirect: "/chat",
  meta: {
    icon: "ep/chat-dot-round",
    title: "智能问答",
    rank: 1
  },
  children: [
    {
      // sessionId 可选：/chat 空态，/chat/:id 指定会话；同组件内 watch 切换
      path: "/chat/:sessionId?",
      name: "ChatPage",
      component: () => import("@/views/chat/index.vue"),
      meta: {
        title: "智能问答",
        keepAlive: true
      }
    }
  ]
} satisfies RouteConfigsTable;
