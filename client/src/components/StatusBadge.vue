<script setup lang="ts">
import { computed } from "vue";
import type { RunStatus } from "../types";

const props = defineProps<{ status: RunStatus | string }>();

const styles: Record<string, string> = {
  pending: "border-line-strong bg-raised text-muted",
  running: "border-info-line bg-info-soft text-info",
  awaiting_user: "border-warning-line bg-warning-soft text-warning",
  completed: "border-success-line bg-success-soft text-success",
  failed: "border-danger-line bg-danger-soft text-danger",
};

const labels: Record<string, string> = {
  pending: "Queued",
  running: "Running",
  awaiting_user: "Handoff — needs you",
  completed: "Completed",
  failed: "Failed",
};

const classes = computed(() => styles[props.status] ?? "border-line-strong bg-raised text-muted");
const label = computed(() => labels[props.status] ?? props.status);
</script>

<template>
  <span class="inline-flex rounded-full border px-3 py-1 text-xs font-semibold" :class="classes">
    {{ label }}
  </span>
</template>
