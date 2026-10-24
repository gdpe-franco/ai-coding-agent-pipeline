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
    <li v-for="event in events" :key="event.id" class="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm sm:flex sm:gap-3">
      <span class="font-mono text-xs text-faint sm:w-36 sm:shrink-0">
        {{ new Date(event.created_at).toLocaleTimeString() }}
      </span>
      <span class="min-w-0 break-words font-mono text-xs uppercase tracking-wide text-accent sm:w-36 sm:shrink-0">
        {{ event.kind }}
      </span>
      <div class="col-span-2 min-w-0 break-words text-paper-dim sm:flex-1">
        <p>{{ summary(event.payload) }}</p>
        <details v-if="Object.keys(event.payload).length" class="mt-1 text-xs text-faint">
          <summary class="cursor-pointer hover:text-paper-dim">Details</summary>
          <pre class="mt-2 max-h-80 max-w-full overflow-auto whitespace-pre-wrap break-words rounded bg-ink p-3 text-paper-dim">{{ payloadJson(event.payload) }}</pre>
        </details>
      </div>
    </li>
    <li v-if="events.length === 0" class="text-sm text-faint">No events yet.</li>
  </ol>
</template>
