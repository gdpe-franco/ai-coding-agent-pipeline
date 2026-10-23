<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { request } from "./api";
import { useAuthStore } from "./stores/auth";
import type { Chat } from "./types";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
const chats = ref<Chat[]>([]);
const sidebarError = ref("");

async function loadChats() {
  if (!auth.authenticated) {
    chats.value = [];
    sidebarError.value = "";
    return;
  }
  try {
    chats.value = await request<Chat[]>("/chats");
    sidebarError.value = "";
  } catch (cause) {
    sidebarError.value = cause instanceof Error ? cause.message : "Could not load chats";
  }
}

async function startNewChat() {
  await router.push("/new-chat");
}

function reloadChats() {
  void loadChats();
}

onMounted(async () => {
  window.addEventListener("chats-updated", reloadChats);
  if (!auth.token) return;
  await auth.fetchMe();
  if (!auth.authenticated) await router.replace("/login");
  else await loadChats();
});

onUnmounted(() => window.removeEventListener("chats-updated", reloadChats));

watch(() => route.path, () => void loadChats());
watch(() => auth.authenticated, () => void loadChats());

function logout() {
  auth.logout();
  void router.push("/login");
}
</script>

<template>
  <div class="min-h-screen lg:flex">
    <aside v-if="auth.authenticated" class="w-full border-b border-slate-800 bg-slate-950 p-4 lg:min-h-screen lg:w-72 lg:border-b-0 lg:border-r">
      <div class="mb-5 flex items-center justify-between gap-3">
        <RouterLink to="/chats" class="font-semibold text-indigo-300">AI Coding Agent</RouterLink>
      </div>
      <button class="btn mb-4 w-full" @click="startNewChat">New chat</button>
      <nav aria-label="Chats" class="space-y-1">
        <p v-if="sidebarError" class="px-3 text-xs text-rose-300" role="alert">{{ sidebarError }}</p>
        <RouterLink
          v-for="chat in chats"
          :key="chat.id"
          :to="`/chats/${chat.id}`"
          class="block truncate rounded-lg px-3 py-2 text-sm hover:bg-slate-800"
          :class="route.params.id === chat.id ? 'bg-slate-800 text-white' : 'text-slate-300'"
        >
          {{ chat.title }}
        </RouterLink>
      </nav>
      <div class="mt-6 flex items-center justify-between gap-2 border-t border-slate-800 pt-4 text-xs text-slate-400">
        <span class="truncate">{{ auth.user?.email }}</span>
        <button class="hover:text-white" @click="logout">Sign out</button>
      </div>
    </aside>
    <div class="min-w-0 flex-1">
      <header v-if="!auth.authenticated" class="border-b border-slate-800 px-4 py-4">
        <RouterLink to="/" class="font-semibold text-indigo-300">AI Coding Agent Pipeline</RouterLink>
      </header>
      <main class="mx-auto max-w-6xl px-4 py-6 lg:py-8">
        <RouterView />
      </main>
    </div>
  </div>
</template>
