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
const loading = ref(false);
const sending = ref(false);
const error = ref("");
let pollTimer: number | undefined;
let refreshPromise: Promise<void> | undefined;
let pendingSubmission: { content: string; key: string } | null = null;

const chatId = computed(() => String(route.params.id));
const latestRunMessageIds = computed(() => {
  const latest = new Map<string, string>();
  for (const message of messages.value) {
    if (message.role === "user" && message.run_id) latest.set(message.run_id, message.id);
  }
  return latest;
});
const stages: Record<string, string> = {
  planner: "Planning the changes",
  coder: "Updating the files",
  reviewer: "Checking the result",
};

function latestActivity(runId: string): string {
  const latest = events.value[runId]?.at(-1);
  if (!latest || latest.kind === "run_queued") return "Waiting to start";
  if (latest.kind === "execution_started") return "Starting work";
  const stepKey = latest.payload.step_key;
  if (typeof stepKey === "string") {
    const stage = stages[stepKey] ?? "Working on your request";
    return latest.kind === "step_completed" ? `${stage} complete` : `${stage}…`;
  }
  if (latest.kind === "artifact_published") return "Preparing your approved preview";
  return "Working on your request";
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
      const sidebarTitleChanged = chat.value === null || chat.value.title !== latestChat.title;
      chat.value = latestChat;
      if (sidebarTitleChanged) window.dispatchEvent(new Event("chats-updated"));
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
    <header class="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
      <div>
        <h1 class="text-xl font-semibold">{{ chat?.title ?? "Project chat" }}</h1>
      </div>
      <div class="flex gap-2">
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
      <p v-if="loading" class="text-sm text-muted" role="status">Loading chat…</p>
      <p v-else-if="messages.length === 0" class="text-sm text-muted">Start with a request for this project.</p>
      <article
        v-for="message in messages"
        :key="message.id"
        class="rounded-xl border p-4 break-words"
        :class="message.role === 'user' ? 'ml-auto w-fit max-w-[85%] border-line-strong bg-hover' : 'w-full max-w-3xl border-line bg-raised'"
        :aria-label="message.role === 'user' ? 'Your message' : 'Assistant message'"
      >
        <p class="whitespace-pre-wrap text-sm leading-6">{{ message.content }}</p>
        <div
          v-if="message.role === 'user' && message.run_id && latestRunMessageIds.get(message.run_id) === message.id"
          class="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-3 text-xs"
        >
          <span v-if="message.run_status" class="flex items-center gap-2 text-muted">
            <StatusBadge :status="message.run_status" />
            <span v-if="isActive(message.run_status)" class="inline-flex items-center gap-2" role="status">
              <span class="h-2 w-2 animate-pulse rounded-full bg-accent" aria-hidden="true" />
              {{ latestActivity(message.run_id) }}
            </span>
          </span>
          <RouterLink class="text-accent hover:text-accent-hover" :to="`/runs/${message.run_id}`">
            View details
          </RouterLink>
        </div>
      </article>
    </div>

    <form class="border-t border-line pt-4" @submit.prevent="send">
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
      <p v-if="error" class="mt-2 text-sm text-danger" role="alert">{{ error }}</p>
    </form>
  </section>
</template>
