<script setup lang="ts">
import { onMounted } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";

const auth = useAuthStore();
const router = useRouter();

onMounted(async () => {
  if (!auth.token) return;
  await auth.fetchMe();
  if (!auth.authenticated) await router.replace("/login");
});

function logout() {
  auth.logout();
  void router.push("/login");
}
</script>

<template>
  <div class="min-h-screen">
    <header class="border-b border-slate-800 bg-slate-950/90">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
        <RouterLink to="/" class="text-lg font-semibold text-indigo-300">AI Coding Agent Pipeline</RouterLink>
        <nav v-if="auth.authenticated" class="flex items-center gap-4 text-sm">
          <RouterLink to="/chat" class="text-slate-300 hover:text-white">Chat</RouterLink>
          <span class="text-slate-500">{{ auth.user?.email }}</span>
          <button class="btn-secondary" @click="logout">Sign out</button>
        </nav>
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-4 py-8">
      <RouterView />
    </main>
  </div>
</template>
