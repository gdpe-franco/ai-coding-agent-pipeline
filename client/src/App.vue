<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { request } from "./api";
import { useAuthStore } from "./stores/auth";
import type { Chat } from "./types";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
const chats = ref<Chat[]>([]);
const desktopSidebarExpanded = ref(true);
const mobileSidebarOpen = ref(false);
const isDesktop = ref(
  typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches,
);
const sidebarError = ref("");
const openMenuChatId = ref<string | null>(null);
const renameChatId = ref<string | null>(null);
const renameTitle = ref("");
const renameError = ref("");
const sidebarElement = ref<HTMLElement | null>(null);
const mobileToggleButton = ref<HTMLButtonElement | null>(null);
const mobileCloseButton = ref<HTMLButtonElement | null>(null);
let desktopMedia: MediaQueryList | undefined;

const sidebarExpanded = computed(() => !isDesktop.value || desktopSidebarExpanded.value);

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
  closeMobileSidebar();
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
  desktopMedia = window.matchMedia("(min-width: 1024px)");
  isDesktop.value = desktopMedia.matches;
  desktopMedia.addEventListener("change", onDesktopChange);
  window.addEventListener("keydown", onGlobalKeydown);
  window.addEventListener("chats-updated", chatsUpdated);
  if (!auth.token) return;
  await auth.fetchMe();
  if (!auth.authenticated) await router.replace("/login");
  else await loadChats();
});

onUnmounted(() => {
  desktopMedia?.removeEventListener("change", onDesktopChange);
  window.removeEventListener("keydown", onGlobalKeydown);
  window.removeEventListener("chats-updated", chatsUpdated);
});

watch(() => route.path, () => {
  openMenuChatId.value = null;
  closeMobileSidebar(false);
  void loadChats();
});
watch(() => auth.authenticated, () => void loadChats());

function logout() {
  auth.logout();
  void router.push("/login");
}

async function openMobileSidebar() {
  mobileSidebarOpen.value = true;
  await nextTick();
  mobileCloseButton.value?.focus();
}

function closeMobileSidebar(restoreFocus = true) {
  if (!mobileSidebarOpen.value) return;
  mobileSidebarOpen.value = false;
  if (restoreFocus) void nextTick(() => mobileToggleButton.value?.focus());
}

function onDesktopChange(event: MediaQueryListEvent) {
  isDesktop.value = event.matches;
  if (event.matches) mobileSidebarOpen.value = false;
}

function onGlobalKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") closeMobileSidebar();
}

function trapMobileFocus(event: KeyboardEvent) {
  if (isDesktop.value || !mobileSidebarOpen.value || event.key !== "Tab") return;
  const focusable = sidebarElement.value?.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  );
  if (!focusable?.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
</script>

<template>
  <div class="flex h-dvh overflow-hidden bg-ink">
    <button
      v-if="auth.authenticated && !isDesktop && mobileSidebarOpen"
      class="fixed inset-0 z-40 cursor-default bg-black/60 lg:hidden"
      type="button"
      aria-label="Dismiss navigation drawer"
      @click="closeMobileSidebar()"
    />

    <aside
      v-if="auth.authenticated"
      ref="sidebarElement"
      id="app-sidebar"
      class="fixed inset-y-0 left-0 z-50 flex h-dvh shrink-0 flex-col border-r border-line bg-ink transition-[width,transform] duration-200 lg:relative lg:translate-x-0"
      :class="[
        isDesktop ? (desktopSidebarExpanded ? 'w-72' : 'w-16') : 'w-72 max-w-[85vw]',
        isDesktop || mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full',
      ]"
      :inert="!isDesktop && !mobileSidebarOpen"
      :aria-hidden="!isDesktop && !mobileSidebarOpen ? 'true' : undefined"
      aria-label="Application sidebar"
      @keydown="trapMobileFocus"
    >
      <div class="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-line px-3">
        <RouterLink
          to="/chats"
          class="flex min-w-0 items-center gap-2 font-mono text-xs font-semibold uppercase tracking-[0.12em] text-accent"
          :aria-label="sidebarExpanded ? 'AI Coding Multi-Agent home' : 'AI Coding Multi-Agent'"
        >
          <img v-if="!sidebarExpanded" src="/favicon.svg?v=2" alt="" class="h-8 w-8 shrink-0 rounded-lg" />
          <span v-if="sidebarExpanded" class="truncate">AI Coding Multi-Agent</span>
        </RouterLink>
        <button
          v-if="isDesktop && desktopSidebarExpanded"
          class="icon-button"
          type="button"
          aria-label="Collapse sidebar"
          aria-controls="app-sidebar"
          @click="desktopSidebarExpanded = false"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M9 4v16" />
          </svg>
        </button>
        <button
          v-else-if="!isDesktop"
          ref="mobileCloseButton"
          class="icon-button"
          type="button"
          aria-label="Close sidebar"
          aria-controls="app-sidebar"
          @click="closeMobileSidebar()"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="m6 6 12 12M18 6 6 18" />
          </svg>
        </button>
      </div>

      <div class="shrink-0 p-3">
        <button
          v-if="sidebarExpanded"
          class="btn w-full justify-start gap-3"
          type="button"
          @click="startNewChat"
        >
          <span class="text-xl leading-none" aria-hidden="true">+</span>
          New chat
        </button>
        <button
          v-else
          class="icon-button mx-auto"
          type="button"
          aria-label="New chat"
          title="New chat"
          @click="startNewChat"
        >
          <span class="text-xl leading-none" aria-hidden="true">+</span>
        </button>
      </div>

      <nav v-if="sidebarExpanded" aria-label="Chats" class="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
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
              @click="closeMobileSidebar()"
            >
              {{ chat.title }}
            </RouterLink>
            <button
              class="icon-button h-8 w-8"
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

      <div class="mt-auto border-t border-line p-3">
        <div v-if="sidebarExpanded" class="flex min-w-0 items-center gap-2">
          <span class="min-w-0 flex-1 truncate text-xs text-paper-dim">{{ auth.user?.email }}</span>
          <button class="rounded px-2 py-1 text-xs text-muted hover:bg-hover hover:text-paper" type="button" @click="logout">
            Sign out
          </button>
        </div>
        <div v-else class="flex justify-center">
          <button class="icon-button h-8 w-8 text-xs" type="button" aria-label="Sign out" title="Sign out" @click="logout">
            ↪
          </button>
        </div>
      </div>
    </aside>

    <div v-if="!auth.authenticated" class="flex min-w-0 flex-1 flex-col">
      <header class="border-b border-line px-4 py-4">
        <RouterLink to="/" class="flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-[0.16em] text-accent">
          <span class="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
          AI Coding Multi-Agent Pipeline
        </RouterLink>
      </header>
      <main class="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5 lg:px-8 lg:py-6">
        <RouterView />
      </main>
    </div>

    <div
      v-else
      class="flex min-h-0 min-w-0 flex-1 flex-col"
      :inert="!isDesktop && mobileSidebarOpen"
      :aria-hidden="!isDesktop && mobileSidebarOpen ? 'true' : undefined"
    >
      <header class="z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-ink px-3 sm:px-5">
        <button
          v-if="!isDesktop && !mobileSidebarOpen"
          ref="mobileToggleButton"
          class="icon-button"
          type="button"
          aria-label="Open sidebar"
          aria-controls="app-sidebar"
          :aria-expanded="mobileSidebarOpen"
          @click="openMobileSidebar"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M9 4v16" />
          </svg>
        </button>
        <button
          v-else-if="!desktopSidebarExpanded"
          class="icon-button"
          type="button"
          aria-label="Open sidebar"
          aria-controls="app-sidebar"
          @click="desktopSidebarExpanded = true"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M9 4v16" />
          </svg>
        </button>
        <span class="sr-only">AI Coding Multi-Agent workspace</span>
      </header>
      <main class="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5 lg:px-8 lg:py-6">
        <RouterView />
      </main>
    </div>
  </div>
</template>
