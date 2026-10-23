import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "./stores/auth";
import LoginView from "./views/LoginView.vue";
import ChatView from "./views/ChatView.vue";
import ChatsHomeView from "./views/ChatsHomeView.vue";
import ChatHistoryView from "./views/ChatHistoryView.vue";
import NewChatView from "./views/NewChatView.vue";
import ProjectView from "./views/ProjectView.vue";
import RunView from "./views/RunView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/chats" },
    { path: "/login", component: LoginView, meta: { public: true } },
    { path: "/chats", component: ChatsHomeView },
    { path: "/new-chat", component: NewChatView },
    { path: "/chats/:id/history", component: ChatHistoryView },
    { path: "/chats/:id", component: ChatView },
    { path: "/projects/:id", component: ProjectView },
    { path: "/runs/:id", component: RunView },
  ],
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (!to.meta.public && !auth.authenticated) {
    return { path: "/login", query: { next: to.fullPath } };
  }
  if (to.path === "/login" && auth.authenticated) {
    return { path: "/chats" };
  }
  return true;
});

export default router;
