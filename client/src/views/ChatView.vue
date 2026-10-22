<script setup lang="ts">
import { onMounted, ref } from "vue";
import { request } from "../api";
import type { Chat } from "../types";

const chat = ref<Chat | null>(null);
const error = ref("");

onMounted(async () => {
  try {
    chat.value = await request<Chat>("/chat");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load your chat";
  }
});
</script>

<template>
  <section class="card mx-auto flex min-h-[70vh] max-w-4xl flex-col" aria-label="Private chat">
    <header class="border-b border-slate-800 pb-4">
      <h1 class="text-xl font-semibold">Chat</h1>
      <p class="mt-1 text-sm text-slate-400">Your private conversation.</p>
    </header>
    <div class="flex-1 space-y-4 overflow-y-auto py-5" role="log" aria-live="polite">
      <p v-if="error" class="text-sm text-rose-300">{{ error }}</p>
      <p v-else-if="chat && chat.turns.length === 0" class="text-sm text-slate-400">
        Your conversation will appear here.
      </p>
      <article
        v-for="turn in chat?.turns ?? []"
        :key="turn.id"
        class="max-w-3xl rounded-lg p-4"
        :class="turn.role === 'user' ? 'ml-auto bg-indigo-950/70' : 'bg-slate-900'"
        :aria-label="turn.role === 'user' ? 'Your message' : 'Assistant message'"
      >
        <p class="whitespace-pre-wrap text-sm leading-6">{{ turn.content }}</p>
      </article>
    </div>
  </section>
</template>
