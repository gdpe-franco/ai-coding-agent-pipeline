<script setup lang="ts">
import type { RunEvent } from "../types";

defineProps<{ events: RunEvent[] }>();

function summary(payload: Record<string, unknown>): string {
  const value = payload.summary ?? payload.message ?? payload.verdict ?? payload.file_path ?? payload.guidance;
  if (typeof value === "string") {
    return value.length > 140 ? `${value.slice(0, 140)}…` : value;
  }
  if (payload.attempt !== undefined) {
    return `attempt ${payload.attempt}`;
  }
  return "";
}
</script>

<template>
  <ol class="space-y-2">
    <li v-for="event in events" :key="event.id" class="flex gap-3 text-sm">
      <span class="w-36 shrink-0 font-mono text-xs text-slate-500">
        {{ new Date(event.created_at).toLocaleTimeString() }}
      </span>
      <span class="w-36 shrink-0 font-mono text-xs uppercase tracking-wide text-indigo-300">
        {{ event.kind }}
      </span>
      <span class="min-w-0 break-words text-slate-300">{{ summary(event.payload) }}</span>
    </li>
    <li v-if="events.length === 0" class="text-sm text-slate-500">No events yet.</li>
  </ol>
</template>
