<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import StatusBadge from "../components/StatusBadge.vue";
import type { Chat, ChatMessage, RunEvent, RunStatus } from "../types";

const route = useRoute();
const chat = ref<Chat | null>(null);
const messages = ref<ChatMessage[]>([]);
const events = ref<Record<string, RunEvent[]>>({});
const draft = ref("");
const titleDraft = ref("");
const loading = ref(false);
const sending = ref(false);
const savingTitle = ref(false);
const editingTitle = ref(false);
const error = ref("");
let pollTimer: number | undefined;
let refreshPromise: Promise<void> | undefined;
let pendingSubmission: { content: string; key: string } | null = null;

const chatId = computed(() => String(route.params.id));

function latestActivity(runId: string): string {
  const latest = events.value[runId]?.at(-1);
  if (!latest) return "Preparing the run…";
  const stepKey = latest.payload.step_key;
  if (typeof stepKey === "string") {
    return `${latest.kind === "step_completed" ? "Finished" : "Working on"} ${stepKey}`;
  }
  return latest.kind.replaceAll("_", " ");
}

function isActive(status: RunStatus | null): boolean {
  return status === "pending" || status === "running";
}

async function refresh(): Promise<void> {
  if (refreshPromise) return refreshPromise;
  loading.value = messages.value.length === 0;
  const currentId = chatId.value;
  const pending = (async () => {
    try {
      const [latestChat, latestMessages] = await Promise.all([
        request<Chat>(`/chats/${currentId}`),
        request<ChatMessage[]>(`/chats/${currentId}/messages`),
      ]);
      if (currentId !== chatId.value) return;
      chat.value = latestChat;
      messages.value = latestMessages;
      const activeRunIds = [
        ...new Set(
          latestMessages
            .filter((message) => message.run_id && isActive(message.run_status))
            .map((message) => message.run_id as string),
        ),
      ];
      const activities = await Promise.all(
        activeRunIds.map(async (runId) => [
          runId,
          await request<RunEvent[]>(`/runs/${runId}/events`),
        ] as const),
      );
      events.value = Object.fromEntries(activities);
      error.value = "";
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : "Could not load this chat";
    } finally {
      loading.value = false;
      refreshPromise = undefined;
    }
  })();
  refreshPromise = pending;
  return pending;
}

async function send() {
  const message = draft.value.trim();
  if (!message || sending.value) return;
  sending.value = true;
  error.value = "";
  try {
    if (pendingSubmission?.content !== message) {
      pendingSubmission = { content: message, key: crypto.randomUUID() };
    }
    await request(`/chats/${chatId.value}/messages`, {
      method: "POST",
      headers: { "Idempotency-Key": pendingSubmission.key },
      body: JSON.stringify({ message }),
    });
    pendingSubmission = null;
    draft.value = "";
    await refresh();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not send your message";
  } finally {
    sending.value = false;
  }
}

async function renameChat() {
  if (!chat.value || !titleDraft.value.trim() || savingTitle.value) return;
  savingTitle.value = true;
  error.value = "";
  try {
    chat.value = await request<Chat>(`/chats/${chatId.value}`, {
      method: "PATCH",
      body: JSON.stringify({ title: titleDraft.value.trim() }),
    });
    editingTitle.value = false;
    window.dispatchEvent(new Event("chats-updated"));
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not rename this chat";
  } finally {
    savingTitle.value = false;
  }
}

watch(chatId, async () => {
  chat.value = null;
  messages.value = [];
  events.value = {};
  if (refreshPromise) await refreshPromise;
  await refresh();
});

onMounted(() => {
  void refresh();
  pollTimer = window.setInterval(() => void refresh(), 3000);
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
});
</script>

<template>
  <section class="card mx-auto flex min-h-[75vh] max-w-4xl flex-col" aria-label="Project chat">
    <header class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-4">
      <div>
        <h1 class="text-xl font-semibold">{{ chat?.title ?? "Project chat" }}</h1>
        <p class="mt-1 text-xs text-slate-500">This chat always works on its own project.</p>
        <form v-if="editingTitle" class="mt-2 flex gap-2" @submit.prevent="renameChat">
          <label :for="`chat-title-${chatId}`" class="sr-only">Chat title</label>
          <input :id="`chat-title-${chatId}`" v-model="titleDraft" class="input max-w-xs" maxlength="200" required />
          <button class="btn-secondary" type="submit" :disabled="savingTitle">Save</button>
          <button class="text-sm text-slate-400" type="button" @click="editingTitle = false">Cancel</button>
        </form>
      </div>
      <div class="flex gap-2">
        <button
          class="btn-secondary text-sm"
          type="button"
          @click="titleDraft = chat?.title ?? ''; editingTitle = true"
        >
          Rename
        </button>
        <RouterLink
          v-if="chat"
          class="btn-secondary text-sm"
          :to="`/projects/${chat.project_id}`"
        >
          Project
        </RouterLink>
        <RouterLink class="btn-secondary text-sm" :to="`/chats/${chatId}/history`">Run history</RouterLink>
      </div>
    </header>

    <div class="flex-1 space-y-4 overflow-y-auto py-5" role="log" aria-live="polite">
      <p v-if="loading" class="text-sm text-slate-400" role="status">Loading chat…</p>
      <p v-else-if="messages.length === 0" class="text-sm text-slate-400">Start with a request for this project.</p>
      <article
        v-for="message in messages"
        :key="message.id"
        class="max-w-3xl rounded-lg p-4"
        :class="message.role === 'user' ? 'ml-auto bg-indigo-950/70' : 'bg-slate-900'"
        :aria-label="message.role === 'user' ? 'Your message' : 'Assistant message'"
      >
        <p class="whitespace-pre-wrap text-sm leading-6">{{ message.content }}</p>
        <div v-if="message.run_id" class="mt-3 flex flex-wrap items-center gap-3 border-t border-slate-700 pt-3 text-xs">
          <span v-if="message.run_status" class="flex items-center gap-2 text-slate-400">
            <StatusBadge :status="message.run_status" />
            <span v-if="isActive(message.run_status)" role="status">{{ latestActivity(message.run_id) }}</span>
          </span>
          <RouterLink class="text-indigo-300 hover:text-indigo-200" :to="`/runs/${message.run_id}`">
            View details
          </RouterLink>
        </div>
      </article>
    </div>

    <form class="border-t border-slate-800 pt-4" @submit.prevent="send">
      <label for="chat-message" class="sr-only">Message</label>
      <div class="flex items-end gap-3">
        <textarea
          id="chat-message"
          v-model="draft"
          class="input min-h-20 flex-1 resize-y"
          maxlength="8000"
          placeholder="Message this project"
          :disabled="sending || loading"
        />
        <button class="btn" type="submit" :disabled="sending || loading || !draft.trim()">
          {{ sending ? "Sending…" : "Send" }}
        </button>
      </div>
      <p v-if="error" class="mt-2 text-sm text-rose-300" role="alert">{{ error }}</p>
    </form>
  </section>
</template>
