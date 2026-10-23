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
const openMenuChatId = ref<string | null>(null);
const renameChatId = ref<string | null>(null);
const renameTitle = ref("");
const renameError = ref("");

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

function chatsUpdated() {
  void loadChats();
}

function beginRename(chat: Chat) {
  renameChatId.value = chat.id;
  renameTitle.value = chat.title;
  renameError.value = "";
  openMenuChatId.value = null;
}

function cancelRename() {
  renameChatId.value = null;
  renameTitle.value = "";
  renameError.value = "";
}

async function saveChatTitle(chat: Chat) {
  const title = renameTitle.value.trim();
  if (!title) return;
  try {
    const renamed = await request<Chat>(`/chats/${chat.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    chats.value = chats.value.map((item) => (item.id === renamed.id ? renamed : item));
    cancelRename();
  } catch (cause) {
    renameError.value = cause instanceof Error ? cause.message : "Could not rename this chat";
  }
}

onMounted(async () => {
  window.addEventListener("chats-updated", chatsUpdated);
  if (!auth.token) return;
  await auth.fetchMe();
  if (!auth.authenticated) await router.replace("/login");
  else await loadChats();
});

onUnmounted(() => window.removeEventListener("chats-updated", chatsUpdated));

watch(() => route.path, () => {
  openMenuChatId.value = null;
  void loadChats();
});
watch(() => auth.authenticated, () => void loadChats());

function logout() {
  auth.logout();
  void router.push("/login");
}
</script>

<template>
  <div class="min-h-screen lg:flex">
    <aside v-if="auth.authenticated" class="w-full border-b border-line bg-ink p-4 lg:min-h-screen lg:w-72 lg:border-b-0 lg:border-r">
      <div class="mb-5 flex items-center justify-between gap-3">
        <RouterLink to="/chats" class="flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-accent">
          <span class="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
          AI Coding Multi-Agent
        </RouterLink>
      </div>
      <button class="btn mb-4 w-full" @click="startNewChat">New chat</button>
      <nav aria-label="Chats" class="space-y-1">
        <p v-if="sidebarError" class="px-3 text-xs text-danger" role="alert">{{ sidebarError }}</p>
        <div v-for="chat in chats" :key="chat.id" class="relative">
          <form
            v-if="renameChatId === chat.id"
            class="space-y-2 rounded-lg bg-raised p-2"
            @submit.prevent="saveChatTitle(chat)"
          >
            <label :for="`sidebar-title-${chat.id}`" class="sr-only">Rename chat</label>
            <input
              :id="`sidebar-title-${chat.id}`"
              v-model="renameTitle"
              class="input text-sm"
              maxlength="200"
              required
              autofocus
              @keydown.esc.prevent="cancelRename"
            />
            <p v-if="renameError" class="text-xs text-danger" role="alert">{{ renameError }}</p>
            <div class="flex justify-end gap-2">
              <button class="text-xs text-muted" type="button" @click="cancelRename">Cancel</button>
              <button class="text-xs text-accent" type="submit">Save</button>
            </div>
          </form>
          <div v-else class="flex items-center gap-1">
            <RouterLink
              :to="`/chats/${chat.id}`"
              class="min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-sm hover:bg-hover"
              :class="route.params.id === chat.id ? 'bg-hover text-paper' : 'text-paper-dim'"
            >
              {{ chat.title }}
            </RouterLink>
            <button
              class="rounded px-2 py-1 text-muted hover:bg-hover hover:text-paper"
              type="button"
              aria-haspopup="menu"
              :aria-label="`Options for ${chat.title}`"
              :aria-expanded="openMenuChatId === chat.id"
              @click="openMenuChatId = openMenuChatId === chat.id ? null : chat.id"
            >
              ⋯
            </button>
          </div>
          <div
            v-if="openMenuChatId === chat.id"
            class="absolute right-0 top-10 z-10 rounded-lg border border-line bg-raised p-1 shadow-xl"
            role="menu"
            @keydown.esc.prevent="openMenuChatId = null"
          >
            <button class="whitespace-nowrap rounded px-3 py-2 text-left text-sm hover:bg-hover" role="menuitem" type="button" @click="beginRename(chat)">
              Rename chat
            </button>
          </div>
        </div>
      </nav>
      <div class="mt-6 flex items-center justify-between gap-2 border-t border-line pt-4 text-xs text-muted">
        <span class="truncate">{{ auth.user?.email }}</span>
        <button class="hover:text-paper" @click="logout">Sign out</button>
      </div>
    </aside>
    <div class="min-w-0 flex-1">
      <header v-if="!auth.authenticated" class="border-b border-line px-4 py-4">
        <RouterLink to="/" class="flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-accent">
          <span class="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
          AI Coding Multi-Agent Pipeline
        </RouterLink>
      </header>
      <main class="mx-auto max-w-6xl px-4 py-6 lg:py-8">
        <RouterView />
      </main>
    </div>
  </div>
</template>
