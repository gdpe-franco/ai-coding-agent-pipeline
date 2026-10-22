<script setup lang="ts">
import { computed } from "vue";
import type { RunStatus } from "../types";

const props = defineProps<{ status: RunStatus | string }>();

const styles: Record<string, string> = {
  pending: "bg-slate-700 text-slate-100",
  running: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  awaiting_user: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  completed: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  failed: "bg-rose-500/20 text-rose-300 border-rose-500/40",
};

const labels: Record<string, string> = {
  pending: "Queued",
  running: "Running",
  awaiting_user: "Handoff — needs you",
  completed: "Completed",
  failed: "Failed",
};

const classes = computed(() => styles[props.status] ?? "bg-slate-700 text-slate-100");
const label = computed(() => labels[props.status] ?? props.status);
</script>

<template>
  <span class="inline-flex rounded-full border border-transparent px-3 py-1 text-xs font-semibold" :class="classes">
    {{ label }}
  </span>
</template>
