import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "./stores/auth";
import LoginView from "./views/LoginView.vue";
import ChatView from "./views/ChatView.vue";
import NewRunView from "./views/NewRunView.vue";
import ProjectView from "./views/ProjectView.vue";
import ProjectsView from "./views/ProjectsView.vue";
import RunView from "./views/RunView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    { path: "/login", component: LoginView, meta: { public: true } },
    { path: "/chat", component: ChatView },
    { path: "/projects", component: ProjectsView },
    { path: "/projects/:id", component: ProjectView },
    { path: "/new", component: NewRunView },
    { path: "/runs/:id", component: RunView },
  ],
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  if (!to.meta.public && !auth.authenticated) {
    return { path: "/login", query: { next: to.fullPath } };
  }
  if (to.path === "/login" && auth.authenticated) {
    return { path: "/chat" };
  }
  return true;
});

export default router;
