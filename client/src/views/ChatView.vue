<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { request } from "../api";
import type { Chat } from "../types";

const chat = ref<Chat | null>(null);
const draft = ref("");
const sending = ref(false);
const error = ref("");
let pollTimer: number | undefined;
let refreshPromise: Promise<void> | undefined;

function refresh(): Promise<void> {
  if (refreshPromise) return refreshPromise;
  const pending = (async () => {
    try {
      const lastSequence = chat.value?.turns.at(-1)?.sequence;
      const cursor = lastSequence === undefined ? "" : `?after=${lastSequence}`;
      const latest = await request<Chat>(`/chat${cursor}`);
      if (!chat.value) {
        chat.value = latest;
      } else {
        const knownTurns = new Set(chat.value.turns.map((turn) => turn.id));
        chat.value = {
          ...latest,
          turns: [
            ...chat.value.turns,
            ...latest.turns.filter((turn) => !knownTurns.has(turn.id)),
          ],
        };
      }
      error.value = "";
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : "Could not load your chat";
    } finally {
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
    await request<{ turn_id: string }>("/chat/messages", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    draft.value = "";
    await refresh();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not send your message";
  } finally {
    sending.value = false;
  }
}

onMounted(() => {
  void refresh();
  pollTimer = window.setInterval(() => void refresh(), 3000);
});

onUnmounted(() => {
  if (pollTimer !== undefined) window.clearInterval(pollTimer);
});
</script>

<template>
  <section class="card mx-auto flex min-h-[70vh] max-w-4xl flex-col" aria-label="Private chat">
    <header class="border-b border-slate-800 pb-4">
      <h1 class="text-xl font-semibold">Chat</h1>
      <p class="mt-1 text-sm text-slate-400">Your private conversation.</p>
    </header>
    <div class="flex-1 space-y-4 overflow-y-auto py-5" role="log" aria-live="polite">
      <p v-if="!error && chat && chat.turns.length === 0" class="text-sm text-slate-400">
        Your conversation will appear here.
      </p>
      <article
        v-for="turn in chat?.turns ?? []"
        :key="turn.id"
        class="max-w-3xl rounded-lg p-4"
        :class="turn.role === 'user' ? 'ml-auto bg-indigo-950/70' : 'bg-slate-900'"
        :aria-label="turn.role === 'user' ? 'Your message' : `${turn.role} message`"
      >
        <p class="whitespace-pre-wrap text-sm leading-6">{{ turn.content }}</p>
        <div
          v-if="turn.run_id || turn.project_id"
          class="mt-3 flex flex-wrap gap-3 border-t border-slate-700 pt-3 text-xs"
        >
          <RouterLink
            v-if="turn.run_id"
            :to="`/runs/${turn.run_id}`"
            class="text-indigo-300 hover:text-indigo-200"
          >
            View related run
          </RouterLink>
          <RouterLink
            v-if="turn.project_id"
            :to="`/projects/${turn.project_id}`"
            class="text-indigo-300 hover:text-indigo-200"
          >
            View related project
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
          placeholder="Write a message"
          :disabled="sending"
        />
        <button class="btn" type="submit" :disabled="sending || !draft.trim()">
          {{ sending ? "Sending…" : "Send" }}
        </button>
      </div>
      <p v-if="error" class="mt-2 text-sm text-rose-300" role="alert">{{ error }}</p>
    </form>
  </section>
</template>
