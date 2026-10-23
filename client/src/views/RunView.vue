<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import EventTimeline from "../components/EventTimeline.vue";
import FileExplorer from "../components/FileExplorer.vue";
import StatusBadge from "../components/StatusBadge.vue";
import type { Run, RunArtifact, RunEvent } from "../types";

const route = useRoute();
const runId = String(route.params.id);
const run = ref<Run | null>(null);
const events = ref<RunEvent[]>([]);
const artifacts = ref<RunArtifact[]>([]);
const selectedExecutionId = ref("");
const error = ref("");
const guidance = ref("");
let pendingContinuation: { guidance: string; key: string } | null = null;
let timer: number | undefined;

const processing = computed(() => run.value?.status === "pending" || run.value?.status === "running");
const artifactExecutions = computed(() => {
  const groups = new Map<string, RunArtifact[]>();
  for (const artifact of artifacts.value) {
    groups.set(artifact.execution_id, [...(groups.get(artifact.execution_id) ?? []), artifact]);
  }
  return [...groups.entries()];
});
const files = computed(() => {
  if (selectedExecutionId.value) {
    return (
      artifactExecutions.value.find(([executionId]) => executionId === selectedExecutionId.value)?.[1] ?? []
    ).map((artifact) => artifact.file_path);
  }
  return (run.value?.latest_code ?? []).map((file) => file.file_path);
});

async function load() {
  try {
    [run.value, events.value, artifacts.value] = await Promise.all([
      request<Run>(`/runs/${runId}`),
      request<RunEvent[]>(`/runs/${runId}/events`),
      request<RunArtifact[]>(`/runs/${runId}/artifacts`),
    ]);
    if (processing.value) selectedExecutionId.value = "";
    else selectedExecutionId.value = artifactExecutions.value.at(-1)?.[0] ?? "";
    error.value = "";
    if (!processing.value) {
      window.clearInterval(timer);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load the run";
  }
}

async function loadFile(path: string): Promise<string> {
  if (!selectedExecutionId.value) {
    return run.value?.latest_code.find((file) => file.file_path === path)?.content ?? "";
  }
  const artifact = artifacts.value.find(
    (item) => item.execution_id === selectedExecutionId.value && item.file_path === path,
  );
  if (!artifact) return "";
  const result = await request<{ content: string }>(`/runs/${runId}/artifacts/${artifact.id}`);
  return result.content;
}

async function continueRun() {
  if (!guidance.value.trim()) {
    return;
  }
  try {
    if (pendingContinuation?.guidance !== guidance.value.trim()) {
      pendingContinuation = { guidance: guidance.value.trim(), key: crypto.randomUUID() };
    }
    await request(`/runs/${runId}/continuations`, {
      method: "POST",
      headers: { "Idempotency-Key": pendingContinuation.key },
      body: JSON.stringify({ guidance: guidance.value.trim() }),
    });
    pendingContinuation = null;
    guidance.value = "";
    await load();
    startPolling();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not continue the run";
  }
}

async function close() {
  if (!window.confirm("Close this handoff? The run will be marked failed and its files are kept.")) {
    return;
  }
  try {
    await request(`/runs/${runId}/close`, { method: "POST" });
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not close the run";
  }
}

function startPolling() {
  window.clearInterval(timer);
  if (!processing.value) return;
  timer = window.setInterval(() => {
    if (processing.value) {
      void load();
    }
  }, 2000);
}

onMounted(async () => {
  await load();
  startPolling();
});

onUnmounted(() => window.clearInterval(timer));
</script>

<template>
  <div v-if="error" class="card flex items-center justify-between gap-3 border-rose-800 text-rose-200">
    <span>{{ error }}</span>
    <button class="btn-secondary" type="button" @click="load">Retry loading</button>
  </div>

  <div v-if="run" class="space-y-6">
    <header class="card flex flex-wrap items-start justify-between gap-4">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-2xl font-semibold">Run</h1>
          <StatusBadge :status="run.status" />
        </div>
        <p class="mt-2 max-w-3xl whitespace-pre-wrap text-sm text-slate-300">{{ run.message }}</p>
        <p class="mt-2 text-xs text-slate-500">{{ new Date(run.created_at).toLocaleString() }}</p>
      </div>
      <div class="flex gap-2">
        <RouterLink class="btn-secondary" :to="`/chats/${run.chat_id}/history`">Run history</RouterLink>
      </div>
    </header>

    <div v-if="run.status === 'awaiting_user'" class="card border-amber-700/60 bg-amber-950/20 space-y-3">
        <h2 class="text-lg font-semibold text-amber-200">This request needs your guidance</h2>
        <p class="text-sm text-amber-100/80">
          The saved files and open questions are shown below. Add guidance to continue this request, or close
          it. This result is not approved.
      </p>
      <textarea v-model="guidance" class="input" rows="3" placeholder="What should change?" />
      <div class="flex gap-2">
        <button class="btn" :disabled="!guidance.trim()" @click="continueRun">Continue with guidance</button>
        <button class="btn-secondary" @click="close">Close run</button>
      </div>
    </div>

    <div v-if="run.chat_reply" class="card text-sm text-slate-200">{{ run.chat_reply }}</div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Plan</h2>
        <p v-if="run.plan" class="text-sm text-slate-300">{{ run.plan.summary }}</p>
        <ol v-if="run.plan" class="space-y-2">
          <li v-for="step in run.plan.steps" :key="step.id" class="rounded-lg border border-slate-800 p-3">
            <p class="font-mono text-sm text-indigo-300">{{ step.file_path }}</p>
            <p class="text-sm text-slate-300">{{ step.description }}</p>
          </li>
        </ol>
        <p v-else class="text-sm text-slate-500">No plan yet.</p>
      </section>

      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Deterministic checks</h2>
        <div v-if="run.checks" class="space-y-1 text-sm">
          <p :class="run.checks.file_structure_ok ? 'text-emerald-300' : 'text-rose-300'">
            File structure: {{ run.checks.file_structure_ok ? "pass" : "fail" }}
          </p>
          <p :class="run.checks.js_syntax_ok ? 'text-emerald-300' : 'text-rose-300'">
            JavaScript syntax: {{ run.checks.js_syntax_ok ? "pass" : "fail" }}
          </p>
          <ul v-if="run.checks.issues.length" class="list-disc pl-5 text-slate-300">
            <li v-for="issue in run.checks.issues" :key="issue">{{ issue }}</li>
          </ul>
        </div>
        <p v-else class="text-sm text-slate-500">Checks run before the reviewer.</p>
      </section>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Review</h2>
        <div v-if="run.feedback" class="space-y-2 text-sm">
          <p class="font-semibold" :class="run.feedback.verdict === 'approve' ? 'text-emerald-300' : 'text-amber-300'">
            {{ run.feedback.verdict }}
          </p>
          <p class="text-slate-300">{{ run.feedback.summary }}</p>
          <ul class="list-disc space-y-1 pl-5 text-slate-300">
            <li v-for="(issue, index) in run.feedback.issues" :key="index">
              <span v-if="issue.file_path" class="font-mono text-xs text-indigo-300">{{ issue.file_path }}: </span>
              {{ issue.message }}
            </li>
          </ul>
        </div>
        <p v-else class="text-sm text-slate-500">No review yet.</p>
      </section>
    </div>

    <FileExplorer title="Files" :files="files" :load-file="loadFile">
      <template #actions>
        <label v-if="artifactExecutions.length" class="flex items-center gap-2 text-xs text-slate-400">
          Saved execution
          <select v-model="selectedExecutionId" class="input w-auto py-1">
            <option value="">Current run output</option>
            <option v-for="([executionId], index) in artifactExecutions" :key="executionId" :value="executionId">
              Execution {{ index + 1 }}
            </option>
          </select>
        </label>
      </template>
    </FileExplorer>

    <section class="card space-y-3">
      <h2 class="text-lg font-semibold">Event timeline</h2>
      <EventTimeline :events="events" />
    </section>
  </div>
</template>
