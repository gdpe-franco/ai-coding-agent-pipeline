<script setup lang="ts">
import type { RunEvent } from "../types";

defineProps<{ events: RunEvent[] }>();

function summary(payload: Record<string, unknown>): string {
  const value =
    payload.summary ??
    payload.message ??
    payload.verdict ??
    payload.step_key ??
    payload.file_path ??
    payload.guidance;
  if (typeof value === "string") {
    return value.length > 140 ? `${value.slice(0, 140)}…` : value;
  }
  if (payload.generation !== undefined) {
    return `generation ${payload.generation}`;
  }
  return "";
}

function payloadJson(payload: Record<string, unknown>): string {
  return JSON.stringify(payload, null, 2);
}
</script>

<template>
  <ol class="space-y-2">
    <li v-for="event in events" :key="event.id" class="flex gap-3 text-sm">
      <span class="w-36 shrink-0 font-mono text-xs text-faint">
        {{ new Date(event.created_at).toLocaleTimeString() }}
      </span>
      <span class="w-36 shrink-0 font-mono text-xs uppercase tracking-wide text-accent">
        {{ event.kind }}
      </span>
      <div class="min-w-0 flex-1 break-words text-paper-dim">
        <p>{{ summary(event.payload) }}</p>
        <details v-if="Object.keys(event.payload).length" class="mt-1 text-xs text-faint">
          <summary class="cursor-pointer hover:text-paper-dim">Details</summary>
          <pre class="mt-2 max-h-80 overflow-auto rounded bg-ink p-3 text-paper-dim">{{ payloadJson(event.payload) }}</pre>
        </details>
      </div>
    </li>
    <li v-if="events.length === 0" class="text-sm text-faint">No events yet.</li>
  </ol>
</template>
