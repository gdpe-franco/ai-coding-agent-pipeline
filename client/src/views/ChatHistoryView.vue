<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import StatusBadge from "../components/StatusBadge.vue";
import type { Chat, Run } from "../types";

const route = useRoute();
const chat = ref<Chat | null>(null);
const runs = ref<Run[]>([]);
const error = ref("");
const chatId = String(route.params.id);

onMounted(async () => {
  try {
    [chat.value, runs.value] = await Promise.all([
      request<Chat>(`/chats/${chatId}`),
      request<Run[]>(`/chats/${chatId}/runs`),
    ]);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load run history";
  }
});
</script>

<template>
  <section class="space-y-5">
    <header class="flex items-center justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold">Run history</h1>
        <p class="mt-1 text-sm text-muted">{{ chat?.title }}</p>
      </div>
      <RouterLink class="btn-secondary" :to="`/chats/${chatId}`">Back to chat</RouterLink>
    </header>
    <p v-if="error" role="alert" class="card text-danger">{{ error }}</p>
    <p v-else-if="runs.length === 0" class="card text-sm text-muted">No runs yet.</p>
    <ol v-else class="space-y-3" aria-label="Runs">
      <li v-for="run in runs" :key="run.id" class="card flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate font-medium">{{ run.message }}</p>
          <p class="mt-1 text-xs text-faint">{{ new Date(run.created_at).toLocaleString() }}</p>
        </div>
        <div class="flex items-center gap-3">
          <StatusBadge :status="run.status" />
          <RouterLink class="text-sm text-accent hover:text-accent-hover" :to="`/runs/${run.id}`">
            View details
          </RouterLink>
        </div>
      </li>
    </ol>
  </section>
</template>
